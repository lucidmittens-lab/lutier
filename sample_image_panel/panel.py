"""
sample_image_panel.py -- preview a source image before/after the current
Stack (see stack_panel.py) is applied, via an interactive wipe comparison
(wipe_compare_widget.py). Also lets you export that graded result as a
real still image file (Export Still), independent of LUT export (see
main_window.py's Export dialog) -- though that dialog's own "Export
Still" checkbox (per Tom) now reuses this panel's
render_full_resolution_after/has_sample_image directly rather than
duplicating the reload/apply sequence, see those methods' docstrings
below.

The actual image loading and file export live in app_logic.py
(load_image_as_array / export_image_array_to_file) and are fully tested
there without Qt; the actual stack application is
stack.apply_stack_to_image_array, fully tested in test_stack.py. This
file is just the widget: QFileDialog for export, and feeding the result
into WipeCompareWidget. Loading a source or reference image from disk
within the Viewer panel was removed (per Tom) -- sources/references now
come from the Media Library instead (see load_sample_from_library_entry/
load_reference_from_library_entry).

PREVIEW HAND-OFF (2026-08-09, GPU project Stage 5) -- this panel now hands
WipeCompareWidget raw (H,W,3) float32 numpy arrays directly (see
_array_and_gpu_stack_for_source/_render_wipe), not QPixmaps -- that widget
uploads them to GL textures itself and, for a GPU-eligible stack (see
gpu_stack_renderer.is_stack_gpu_eligible), grades them on the GPU inside
its own paintGL rather than this panel precomputing the result on the
CPU. A GPU-INELIGIBLE stack (e.g. a "lut" stage with a non-default domain,
a "printer_lights" stage whose working_colourspace has no unambiguous
native-linear sibling, or a fork) still gets its graded result computed
here on the CPU exactly as before (_recompute_sample_after,
apply_stack_to_image_array) and handed
over as a plain already-processed array. Superseded an earlier (2026-07-
25) direct numpy-array-to-QPixmap conversion (_array_to_pixmap, since
removed) that itself had replaced a PNG-encode round trip -- see
RUNNING.md for that history; array_to_png_bytes (app_logic.py) is
untouched either way, still exactly right for Export Still's actual
file-writing job.

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

`_image_array` (the "working" array everything else in this file actually
uses -- before/after preview, scopes, Export Still) is always derived
from `_original_array` (exactly what was decoded off disk, [0,1] raw code
values, never touched again after load) via _recompute_working_array(),
never mutated in place -- same non-destructive principle the Stack itself
follows. Color-space adaptation (a "declare a source space, convert into
a different one before preview" dialog) used to live here too but was
removed (per Tom) -- sources/references are now used as decoded, no
implicit reinterpretation step.

HEADER UI (per Tom): a single "..." menu button holds "Export Still.../
Load Default Source" instead of separate buttons -- same toolbar-space-
saving direction as the Stack panel's Grid Size/Domain popup. "Load
Default Source" (restore_default_image) replaced the old destructive
Clear -- there's always a sensible image to fall back to instead of an
empty panel, so "clear to nothing" wasn't pulling its weight as its own
action. The file path itself is intentionally never displayed (also per
Tom) -- load failures surface via a QMessageBox instead of a persistent
label; the one thing still shown inline (status_label, non-modal, since
it can fire on every live edit) is a stack-apply failure message, which
isn't about the path at all.

PySide6 + stub libEGL.so.1 are installed now (see browser_panel.py's
docstring) -- menu_button no longer builds its own header row (see
_build_layout below); MainWindow now places it as PanelFrame's
corner_widget on the "Viewer" title row instead, confirmed headless by
walking the constructed widget tree. Actual pixel rendering/import/export
below is still unverified in this offscreen environment. Please run
main_window.py, load a source image from the Media Library, build a
stack with a couple of stages, confirm the preview updates live as you
add/reorder/toggle/adjust opacity on stages, then try Export Still and
confirm the saved file is full resolution and matches what the "after"
side of the wipe compare showed. Also confirm: the app launches with the
bundled default reference image already loaded; the "..." menu's Export
Still/Load Default Source both work; Load Default Source reloads the
bundled image after you've loaded something else from the Media Library;
no file path is ever shown anywhere in this panel.
"""
from pathlib import Path


from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMessageBox, QToolButton, QMenu, QFrame,
)

from wipe_compare_widget import WipeCompareWidget
from app_logic import (
    load_image_as_array, default_sample_image_path,
)
from stack import StageCache
import video_playback_controller
import scopes_worker
import timeline_playback_controller
from casting_controller import CastingController
from airplay_output_sink import AirPlayOutputSink
from .constants import (
    MODE_LABELS,
    WIPE_SOURCE_CHOICES, WIPE_SOURCE_SAMPLE_AFTER,
    WIPE_SOURCE_SAMPLE_BEFORE,
)
from .transport import TransportMixin
from .video import VideoMixin
from .rendering import RenderMixin

class SampleImagePanel(TransportMixin, VideoMixin, RenderMixin, QWidget):
    """Emits after_image_changed(array_or_None) every time the previewed
    "after" result is (re)computed -- ScopesPanel subscribes to this so its
    histogram/waveform/vectorscope always reflect whatever's currently
    shown in the wipe compare, without duplicating any of the stack
    resolution logic that already lives here.

    Emits image_loaded(width, height) once per successful SAMPLE (not
    Reference) load -- MainWindow uses this to seed Canvas Settings from
    the first thing loaded in a session, see canvas.py's docstring and
    MainWindow._on_sample_image_loaded. Also fires for a VIDEO load's
    first frame (see load_video) -- Canvas Settings seeding doesn't care
    which kind of media triggered it.

    Emits video_loaded(frame_count, frame_rate) once per successful video
    open -- the transport UI (see video_playback_controller.py) uses this
    to size its own scrub range."""

    after_image_changed = Signal(object)
    image_loaded = Signal(int, int)
    video_loaded = Signal(int, float)
    # name, source_path, media_kind, in_frame, out_frame -- see
    # _on_video_add_to_sequence_clicked's own docstring. Same "this
    # panel has no idea TimelinePanel exists, MainWindow wires them
    # together" arrangement as MediaPanel.add_to_timeline_requested.
    add_source_range_to_timeline_requested = Signal(str, str, str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = None
        # None when Sample is a plain still image -- a real, open
        # VideoSource when it's parked on one frame of a loaded video
        # (see load_video/_load_video_frame_core). Grading review reuses
        # the EXACT SAME downstream pipeline either way (Tom's own
        # confirmed scope: parked-frame review only, no separate video
        # grading path) -- this only affects HOW _original_array gets
        # populated, never what happens to it afterward.
        self._video_source = None
        self._video_frame_index = 0
        # Guards _on_video_scrub_slider_changed against re-firing a seek
        # when THIS panel programmatically moves the slider (e.g. every
        # playback tick, or after a step-button click) -- only a REAL
        # user drag on the slider should ever trigger a new seek.
        self._suppress_video_scrub_signal = False
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
        # A cheap, strided (nearest-neighbor, zero interpolation cost)
        # decimation of _image_array by EDIT_DOWNRES_FACTOR, kept in sync
        # with it in _recompute_working_array -- see refresh_preview/
        # _recompute_sample_after below for what actually uses this: a
        # temporarily reduced-resolution stack apply during a rapid burst
        # of live edits, so dragging a slider stays responsive even on an
        # expensive multi-stage stack at native image resolution. Its own
        # StageCache (_preview_cache_downres) is a SEPARATE instance, not
        # a second input_key on _preview_cache -- StageCache's input_key
        # is just this generation counter, which doesn't by itself
        # distinguish "native array, this generation" from "downres
        # array, this generation"; sharing one cache between two
        # differently-shaped arrays under the same key would hand back
        # wrongly-shaped cached blocks.
        self._image_array_downres = None
        self._preview_cache_downres = StageCache()
        # A more aggressive, fixed-target-SIZE decimation (see
        # SCOPES_MAX_DIMENSION's own comment) used INSTEAD of the above
        # whenever the current stack is GPU-eligible -- the Viewer never
        # reads this array in that case (it renders on the GPU at native
        # resolution directly), only ScopesPanel does, so this can stay
        # small unconditionally rather than only during a burst. Own
        # StageCache for the same "differently-shaped arrays need their
        # own cache" reason _preview_cache_downres's own comment gives.
        self._image_array_scopes = None
        self._preview_cache_scopes = StageCache()
        self._stack_getter = None  # zero-arg callable -> the live stack.Stack
        # zero-arg callables -> the current timeline.Timeline and the
        # Sequence Stack (StackPanel.get_sequence_stack, NOT get_stack --
        # see stack_panel.py's own docstring on why those two can diverge
        # while Shot mode is active) -- both wired from MainWindow, read
        # by self._timeline_controller (built below) once per tick, same
        # "callback, not a stored reference" reasoning as _stack_getter.
        self._timeline_getter = None
        self._sequence_stack_getter = None
        # zero-arg callable -> the currently Highlight-toggled stage's own
        # id, or None -- see set_highlight_getter/_array_and_gpu_stack_
        # for_source's own "MATTE HIGHLIGHT" comment.
        self._highlight_getter = None
        self._current_after_array = None  # last successfully rendered "after" result, or None
        # Whether _current_after_array was rendered from the EDIT_
        # DOWNRES_FACTOR-halved array (see _recompute_sample_after) --
        # _on_editing_settled reads this to know whether a final full-
        # resolution recompute is owed. Only ever True for a GPU-
        # INeligible stack's burst path -- a GPU-eligible stack always
        # recomputes at scopes resolution (see refresh_preview's own
        # comment) and never sets this, since there's no higher-
        # resolution version of THAT array to catch up to.
        self._last_after_was_downres = False

        # Reference image state -- deliberately a full parallel set of
        # fields, not a shared/parameterized structure with the Source
        # fields above. See this module's docstring: Reference is the
        # Stack-never-touches-it second image, with its own independent
        # array/path tracking, so it can be loaded/resized without ever
        # affecting Source.
        self._reference_current_path = None
        self._reference_original_array = None
        self._reference_image_array = None

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
        # Starts disabled per direct feedback -- the app should open on a
        # plain single image, not the wipe divider; Ctrl+W/the "..." menu
        # turn it on.
        self._wipe_enabled = False
        self._wipe_left_key = WIPE_SOURCE_SAMPLE_BEFORE
        self._wipe_right_key = WIPE_SOURCE_SAMPLE_AFTER
        self._wipe_enabled_actions = []  # every "Enable Wipe" QAction built so far
        self._wipe_left_actions = []  # [(QAction, key), ...] across every "Left" submenu built so far
        self._wipe_right_actions = []  # ditto for every "Right" submenu

        # Casting (see casting_controller.py's own docstring for the
        # "fork 3 ways from one shared source" design) -- frame_getter
        # reads self.compare INDIRECTLY (a lambda, not the value itself)
        # since self.compare isn't built until _build_layout() below runs
        # -- constructing CastingController here would otherwise capture
        # a permanently-missing widget reference.
        self._casting_controller = CastingController(
            lambda: self.compare.read_back_after_frame(),
            parent=self)
        self._casting_actions = []  # every "Open Independent Viewer Window" QAction built so far
        self._independent_viewer_sink = None  # the ONE AirPlayOutputSink this menu item owns, or None

        self._build_layout()

        # Settle-detection for the temporary editing-time downres (see
        # EDIT_DOWNRES_FACTOR/refresh_preview) -- NOT a resize/zoom-driven
        # reload (see this module's "PREVIEW RESOLUTION" comment above on
        # why that mechanism is gone). isActive() doubles as this class's
        # only "is a burst currently in progress" flag -- see
        # refresh_preview, which starts/restarts this on every Stack edit.
        self._editing_settle_timer = QTimer(self)
        self._editing_settle_timer.setSingleShot(True)
        self._editing_settle_timer.timeout.connect(self._on_editing_settled)

        # Settle-detection for the temporary scrub-time downres (see
        # SCRUB_DOWNRES_FACTOR/_on_video_scrub_slider_changed) -- same
        # shape as _editing_settle_timer above, its own separate timer
        # since "was the last scrub tick part of a burst" and "was the
        # last Stack edit" are unrelated questions that can be true at
        # once (e.g. scrubbing while Highlight is toggled on a stage).
        self._scrub_settle_timer = QTimer(self)
        self._scrub_settle_timer.setSingleShot(True)
        self._scrub_settle_timer.timeout.connect(self._on_scrub_settled)
        self._last_scrub_frame_index = None  # which frame to re-decode at full quality once settled
        self._last_scrub_was_downres = False

    def _build_layout(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # PanelFrame provides the outer gutter

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
        # follows for Export Still/Load Default Source above.
        #
        # No color_readout_label / wipe-usage hint row anymore (per Tom) --
        # a "Color: ..." overlay chip painted directly on the image used to
        # live here (_update_color_readout/WipeCompareWidget.
        # set_color_readout_text), but was removed outright per direct
        # feedback while iterating on the Viewer. The "drag the handle..."
        # hint line is just gone too, not moved.
        # SOURCE/RECORD mode TOGGLE (per direct feedback: "we need to
        # ... easily tell if we are looking at the timeline or not",
        # then a follow-up round: "it should be a toggle you can control
        # all the time and play should work for either" -- NOT an
        # automatic indicator driven by play/pause, a manual switch the
        # user owns, which also decides which controller the transport
        # bar drives -- see _active_transport_controller/_on_video_play_
        # pause_clicked/_on_video_loop_toggled/_on_video_speed_changed
        # below). A bordered frame + a checkable toggle button around
        # self.compare -- see style.py's QFrame#viewerModeFrame/
        # #viewerModeBadge rules for the actual SOURCE (quiet/neutral)
        # vs RECORD (accent-colored, same "this one's active" language
        # already used for a selected Stack row -- deliberately NOT red;
        # per Tom, "it's not like arming a recording in a DAW") styling.
        # Deliberately a wrapping frame + a plain button row, not an
        # overlay painted onto self.compare's own GL surface -- no
        # per-frame render-pipeline changes, no manual overlay
        # positioning/resize-event math to get wrong.
        self._viewer_mode_frame = QFrame()
        self._viewer_mode_frame.setObjectName("viewerModeFrame")
        viewer_mode_layout = QVBoxLayout(self._viewer_mode_frame)
        viewer_mode_layout.setContentsMargins(0, 0, 0, 0)
        viewer_mode_layout.setSpacing(0)

        self._viewer_mode_badge = QToolButton()
        self._viewer_mode_badge.setObjectName("viewerModeBadge")
        self._viewer_mode_badge.setCheckable(True)
        # CLIP / TIMELINE (were SOURCE / RECORD -- renamed when the separate
        # Source viewer arrived, source_viewer.py; this whole panel is now
        # the Record viewer).
        self._viewer_mode_badge.setText(MODE_LABELS["source"])
        self._viewer_mode_badge.setToolTip(
            "Switch between CLIP (grade the single loaded/selected clip, no "
            "timeline needed) and TIMELINE (the Timeline's own composited, "
            "multi-clip playback) -- Play/Pause/Loop/Speed below always act "
            "on whichever one this is currently set to.")
        self._viewer_mode_badge.toggled.connect(self._on_viewer_mode_toggled)
        viewer_mode_layout.addWidget(self._viewer_mode_badge, alignment=Qt.AlignmentFlag.AlignLeft)
        # Hidden (Tom, 2026-10-05: "get rid of the timeline / clip border +
        # indicator in the record viewer") -- the mode now follows what you
        # do: timeline work -> TIMELINE, "Grade in Record" -> CLIP (see
        # activate_record_mode / activate_clip_mode). Kept as the internal
        # toggle those (and tests) drive.
        self._viewer_mode_badge.setVisible(False)

        self.compare = WipeCompareWidget()
        viewer_mode_layout.addWidget(self.compare, stretch=1)

        layout.addWidget(self._viewer_mode_frame, stretch=1)
        # Plain attribute here, NOT _set_viewer_mode(...) -- that method
        # touches self._video_scrub_slider/self._timeline_controller,
        # neither of which exist yet at this point in construction (both
        # built further below). The real initial badge/frame/scrub-bar
        # refresh happens once everything it needs exists, at the very
        # end of this method.
        self._viewer_mode = "source"
        self._viewer_mode_frame.setProperty("viewerMode", "source")

        self._build_video_transport_bar(layout)
        # Grades the Scopes sample off the UI thread during playback (see
        # scopes_worker.py and video_playback_controller.push_graded_frame_to_viewer).
        self._scopes_worker = scopes_worker.ScopesWorker(self)
        self._scopes_worker.result_ready.connect(self._on_async_scopes_result)
        self._playback_controller = video_playback_controller.VideoPlaybackController(self, parent=self)
        self._playback_controller.frame_changed.connect(self._on_video_frame_changed)
        self._playback_controller.playback_stopped.connect(self._on_video_playback_stopped)
        # Transport (Play/Pause/Loop/Speed) drives WHICHEVER controller
        # matches the SOURCE/RECORD toggle above (see
        # _active_transport_controller) -- _playback_controller (this
        # one) is SOURCE, the single directly-loaded/selected clip; the
        # scrub bar always seeks through it too regardless of the
        # current toggle state (see _on_video_scrub_slider_changed),
        # same as before this toggle existed. The getters below are
        # indirection lambdas (not bound directly) because set_timeline_
        # getter/set_sequence_stack_getter are called later, from
        # MainWindow, well after this constructor returns.
        self._timeline_controller = timeline_playback_controller.TimelineController(
            self,
            timeline_getter=lambda: self._timeline_getter() if self._timeline_getter else None,
            sequence_stack_getter=lambda: self._sequence_stack_getter() if self._sequence_stack_getter else None,
            parent=self)
        self._timeline_controller.playback_stopped.connect(self._update_video_play_button_text)
        self._timeline_controller.frame_changed.connect(self._on_timeline_frame_changed)
        self._build_video_keyboard_shortcuts()
        # Deferred from the plain self._viewer_mode = "source" assignment
        # above -- everything these two touch (the scrub slider, the
        # timecode labels, both playback controllers) exists now.
        # Harmless empty state at this point (no video/Timeline loaded
        # yet); real values get set the first time either one is.
        self._refresh_scrub_slider_for_mode()
        self._refresh_source_timecode_for_mode()

    # ---- J/K/L shuttle (transport_logic.Shuttle) ----------------------------

    def keyPressEvent(self, event):
        key = event.key()
        if event.isAutoRepeat() and key in (Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L):
            return
        if key == Qt.Key.Key_K:
            self._k_held = True
            self._shuttle_key("k")
        elif key in (Qt.Key.Key_J, Qt.Key.Key_L):
            self._shuttle_key("j" if key == Qt.Key.Key_J else "l")
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_K and not event.isAutoRepeat():
            self._k_held = False
        super().keyReleaseEvent(event)

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
        # No "Load Source Image.../Load Video.../Load Reference Image..."
        # here anymore (per Tom: "remove the ability to load a source or
        # reference from disk within the viewer panel") -- the Media
        # Libraries panel (media_panel.py, "Load as Source"/
        # "Load as Reference") is now the sole ingestion path for both
        # slots; this panel only ever gets content pushed into it, never
        # pulls it directly from a file dialog itself. "Load Default
        # Source" survives as the one exception -- it's a reset-to-known-
        # state action (the one bundled asset this app ships with), not a
        # user-chosen-file import.
        menu.addAction("Export Still...", self.export_still)
        menu.addSeparator()
        # Replaces the old destructive Clear -- see this module's docstring.
        menu.addAction("Load Default Source", self.restore_default_image)
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

        menu.addSeparator()
        # Opens a plain, independent native window showing the Stack's
        # current, full-resolution graded output -- NOT an "AirPlay Cast"
        # feature with any in-app device picker (no documented API exists
        # for that against a custom frame source, confirmed directly --
        # see casting_controller.py/airplay_output_sink.py's own
        # docstrings). What the window is actually used for -- AirPlay via
        # macOS's own "Use As Separate Display" screen, a plain second
        # monitor, anything else -- is entirely up to the user; this menu
        # item just opens/closes it. Disabled with an explanatory tooltip
        # if PyObjC/AVFoundation aren't available (non-macOS, or PyObjC
        # not installed).
        cast_action = menu.addAction("Open Independent Viewer Window")
        cast_action.setCheckable(True)
        cast_action.setChecked(self._casting_controller.is_casting)
        available = AirPlayOutputSink().is_available()
        cast_action.setEnabled(available)
        if not available:
            cast_action.setToolTip("The independent viewer window needs PyObjC (pyobjc-framework-AVFoundation/AVKit/CoreMedia/Quartz) and macOS.")
        cast_action.toggled.connect(self._on_cast_toggled)
        self._casting_actions.append(cast_action)

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

    def load_image(self, path):
        """Load, downsample, and preview an image. Returns True on
        success. Safe to call directly without going through a file
        dialog."""
        return self._load_image_core(path)

    def restore_default_image(self):
        """"Load Default Source" menu action -- (re)loads the bundled
        default reference image fresh, same as what happens automatically
        on first launch. Replaces the old destructive Clear -- see this
        module's docstring."""
        path = default_sample_image_path()
        if not path.exists():
            QMessageBox.warning(
                self, "Default image missing",
                f"Couldn't find the bundled default reference image at:\n{path}")
            return
        self.load_image(str(path))

    def set_canvas_aspect_ratio(self, ratio):
        """Passthrough to WipeCompareWidget.set_canvas_aspect_ratio -- the
        panel boundary MainWindow calls through (see canvas.py's docstring
        and MainWindow._apply_canvas_settings/_on_sample_image_loaded/
        _new_session/_open_session, every place self._canvas can change)
        rather than reaching into self.compare directly."""
        self.compare.set_canvas_aspect_ratio(ratio)

    def _load_image_core(self, path):
        self._close_video_source()  # a still-image load always replaces any open video, see that method
        try:
            array = load_image_as_array(path, max_dimension=None)  # native res -- see this module's "PREVIEW RESOLUTION" comment
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to load image", f"{path}\n\n{e}")
            self._current_path = None
            self._original_array = None
            self._image_array = None
            self.status_label.setText("")
            self.compare.clear()
            return False

        self._current_path = str(path)
        self._original_array = array
        self.status_label.setText("")
        self._recompute_working_array()
        self._recompute_sample_after()
        self._render_wipe()
        self._casting_controller.on_stack_or_image_changed()
        self.image_loaded.emit(array.shape[1], array.shape[0])
        return True

    def _load_reference_image_core(self, path):
        """Mirrors _load_image_core, but for the Reference slot -- see this
        module's docstring on why Reference has its own fully independent
        state (own array, own path tracking) rather than reusing
        Source's."""
        try:
            array = load_image_as_array(path, max_dimension=None)  # native res -- see this module's "PREVIEW RESOLUTION" comment
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to load reference image", f"{path}\n\n{e}")
            return False

        self._reference_current_path = str(path)
        self._reference_original_array = array
        self._recompute_reference_working_array()
        self._render_wipe()
        return True

    def load_sample_from_library_entry(self, entry):
        """Wired to MediaPanel.load_as_sample_requested. A media_library.
        MediaEntry never carries decoded pixel data (see that module's
        docstring) -- both branches here decode fresh from
        entry.source_path, via the exact same path-only entry points
        already used for a plain "Load as Source" from disk. A VIDEO
        entry routes to load_video (full transport bar, not a frozen
        still); a STILL entry routes to load_image, which decodes at
        native resolution itself."""
        self.activate_clip_mode()
        if entry.is_video:
            self.load_video(entry.source_path)
        else:
            self.load_image(entry.source_path)

    def load_reference_from_library_entry(self, entry):
        """Wired to MediaPanel.load_as_reference_requested -- see
        load_sample_from_library_entry, identical idea for the Reference
        slot: entry.source_path is decoded fresh via load_reference_from_
        path, the same path-only entry point already used for a
        GalleryStill (see that method's own docstring)."""
        self.load_reference_from_path(entry.source_path)

    def load_reference_from_path(self, path):
        """Decodes `path` fresh and loads it into the Reference slot --
        same underlying work as load_reference_from_library_entry, but a
        stable public entry point for callers that only have a PATH, not
        a MediaEntry. Used by stack_memories_panel.py's Stills Gallery
        (Phase 3, per Tom): a GalleryStill is disk-referenced only, same
        as a MediaEntry (see media_library.py's docstring), so this
        always decodes fresh rather than reusing a cached array -- there
        never is one to reuse."""
        return self._load_reference_image_core(path)

    def load_reference_from_array(self, array):
        """Loads an ALREADY-DECODED array directly into the Reference
        slot -- used for a StackMemory's captured full_still (Stills
        Gallery, Phase 3 follow-up, per Tom: "load the stack memory as
        reference"), which has no source file to reload from at all
        (it's a graded snapshot, decoded from PNG bytes by the caller --
        see app_logic.png_bytes_to_array), unlike load_reference_from_
        path's GalleryStill case."""
        self._reference_current_path = None
        self._reference_original_array = array
        self._recompute_reference_working_array()
        self._render_wipe()

    def set_stack_getter(self, getter):
        """getter: a zero-arg callable returning StackPanel's live
        stack.Stack object. A callback rather than a stored reference so
        preview always reflects the stack (stages, order, opacity,
        enabled state, everything) exactly as it stands right now."""
        self._stack_getter = getter

    def set_timeline_getter(self, getter):
        """getter: a zero-arg callable returning the current timeline.
        Timeline -- wired from MainWindow to TimelinePanel.get_timeline.
        Read by self._timeline_controller once per tick, and by
        refresh_video_transport_visibility to decide whether the
        transport bar should be showing at all."""
        self._timeline_getter = getter
        self.refresh_video_transport_visibility()

    def set_sequence_stack_getter(self, getter):
        """getter: a zero-arg callable returning the Sequence Stack
        (StackPanel.get_sequence_stack) -- what self._timeline_controller
        composes every clip's own Stack against each tick (stack.
        compose_stacks). Deliberately separate from set_stack_getter
        above, which returns whichever Stack StackPanel's editor
        currently shows (Sequence OR a clip's own, see stack_panel.py's
        docstring) -- Timeline playback must always use Sequence
        regardless of that toggle's current position."""
        self._sequence_stack_getter = getter

    def set_highlight_getter(self, getter):
        """getter: a zero-arg callable returning the Stage.id currently
        Highlight-toggled in StackPanel's own Keyer (or other matte-
        producing) inspector, or None -- same "callback, not a stored
        value" convention set_stack_getter uses just above, for the
        same reason (StackPanel's own toggle state can change without
        this panel needing to be told explicitly every time). See
        _array_and_gpu_stack_for_source's own "MATTE HIGHLIGHT" comment
        for where this is actually read."""
        self._highlight_getter = getter

    def has_sample_image(self):
        """Whether a sample image is currently loaded -- used by
        export_still() itself and by MainWindow's Export dialog "Export
        Still" checkbox (per Tom) to check the same precondition without
        reaching into self._current_path directly from outside this
        class."""
        return self._current_path is not None

    def clear(self):
        """No longer exposed in the UI (see restore_default_image/the
        "Load Default Source" menu action, which replaced the old Clear
        button) -- kept as a public method since clearing to nothing is still a
        reasonable thing for other code (e.g. a future session-load) to
        want, just not a standalone user-facing action anymore. Used by
        MainWindow._new_session now that a new session starts empty."""
        self._close_video_source()
        self._current_path = None
        self._original_array = None
        self._image_array = None
        self.status_label.setText("")
        self.compare.clear()
        self._set_current_after_array(None)

    @property
    def current_path(self):
        return self._current_path

    # ---- session save/load (see session_io.py) -----------------------

    def get_session_state(self):
        """Plain JSON-able dict describing the currently loaded Source and
        Reference images, or None if Source isn't loaded (Reference-only,
        no Source, isn't a state this app's startup flow produces) --
        used by main_window.py's Save Session. Deliberately just paths +
        tags, never pixel data -- same "how to rebuild it, not the built
        thing" principle as LUT stage provenance in stack_panel.py.
        Reference's path (see media_library.py's docstring on the
        Media-Library-vs-LUT/CDL-Library difference) genuinely can go
        missing across a save/reload if its source file has since moved
        -- unlike Source, there's no bundled default to gracefully fall
        back to, so a missing Reference on load just stays empty (with a
        warning), not a hard failure for the rest of the session. Wipe
        enable/side-selection state is deliberately NOT included here --
        same "session is project state, not screen layout" call this
        module's docstring already makes for window size/panel
        visibility; it resets to Enable Wipe / Source Before / Source
        After on every session load."""
        if self._current_path is None:
            return None
        state = {
            "path": self._current_path,
        }
        if self._video_source is not None:
            # Source is currently a parked VIDEO frame, not a still (Video
            # Support, Tom 2026-08-13) -- "path" above is the video file's
            # own path (see _load_video_frame_core), which load_session_
            # state must route through load_video, not the still-image
            # decoder (OIIO can't read a video container -- that's a real
            # bug this flag fixes, caught by round-trip testing a saved
            # video Source). video_frame_index restores parked position.
            state["is_video"] = True
            state["video_frame_index"] = self._video_frame_index
        if self._reference_current_path is not None:
            state["reference"] = {
                "path": self._reference_current_path,
            }
        return state

    def load_session_state(self, state):
        """Restore a saved Source (and, if present, Reference) state.
        Falls back to the bundled default image (with a warning) if the
        saved Source path no longer exists, same "don't let a missing
        reference wreck the rest of the session" treatment session_io.py
        uses for LUT stages. A missing Reference path degrades more
        simply -- there's no bundled default for it, so it's just left
        empty, with a warning. No-ops on state=None (a session saved with
        no source image loaded). Tolerates (ignores) a pre-removal
        session's leftover "source_colourspace"/"adapt_enabled"/
        "adapt_target_colourspace" keys -- see this module's own docstring
        on Adapt having been removed entirely (per Tom: "remove the
        ability to adapt reference or source")."""
        if not state or not state.get("path"):
            return
        path = state["path"]
        if not Path(path).exists():
            QMessageBox.warning(
                self, "Source image missing",
                f"Couldn't find the session's source image at:\n{path}\n\n"
                "Falling back to the default reference image.")
            self.restore_default_image()
            return
        if state.get("is_video"):
            # See get_session_state's own note -- a video Source must
            # reopen through load_video (real decode), never
            # _load_image_core (OIIO, can't read a video container).
            if not self.load_video(path):
                return
            self.seek_video_frame(state.get("video_frame_index", 0))
        else:
            if not self._load_image_core(path):
                return

        reference_state = state.get("reference")
        if reference_state and reference_state.get("path"):
            ref_path = reference_state["path"]
            if not Path(ref_path).exists():
                QMessageBox.warning(
                    self, "Reference image missing",
                    f"Couldn't find the session's reference image at:\n{ref_path}\n\n"
                    "Leaving Reference empty.")
            else:
                self._load_reference_image_core(ref_path)

        self._recompute_sample_after()
        self._render_wipe()
