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
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Signal, QTimer, Qt
from PySide6.QtGui import QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QMessageBox, QDialog, QComboBox, QCheckBox,
    QToolButton, QMenu, QFrame,
)

from wipe_compare_widget import WipeCompareWidget
from video_scrub_slider import VideoScrubSlider
from app_logic import (
    load_image_as_array, export_image_array_to_file, default_sample_image_path,
)
from stack import apply_stack_to_image_array, running_array_before_stage, StageCache
from floating_dialog import make_floating_tool_window, show_floating
import gpu_stack_renderer
import video_source
import video_playback_controller
import timeline_playback_controller
import timecode
from casting_controller import CastingController
from airplay_output_sink import AirPlayOutputSink

# PREVIEW RESOLUTION (2026-08-09 rework, see RUNNING.md and
# GPU_ACCELERATION_PLAN_2026-08-08.md's "band-aid" note) -- Sample/
# Reference now always load at NATIVE resolution (max_dimension=None to
# load_image_as_array), no capped preview size and no reactive zoom/
# resize-triggered reload. WipeCompareWidget's own _image_rect() already
# scales whatever pixmap it's handed to fit the panel at the current zoom
# (fit_scale * zoom) -- that scaling is all panel-size/zoom ever needed to
# be handled by; re-decoding the SOURCE at a bigger size in response to
# zooming in was solving the same problem a second, redundant way, and
# per Tom, it read as an obvious performance band-aid (a visible reload
# pop when zooming) standing in for a problem it wasn't actually the
# right tool for. What actually costs real time at native resolution is
# apply_stack_to_image_array itself during a live edit -- see
# EDIT_DOWNRES_FACTOR/refresh_preview below, which is the real fix for
# that: temporary reduced-resolution STACK APPLY during a rapid burst of
# edits, not a reduced-resolution SOURCE decode tied to zoom.
EDIT_DOWNRES_FACTOR = 2
# Long-edge cap (pixels) for the array a GPU-eligible stack's CPU
# recompute uses -- see refresh_preview's own comment, 2026-08-10
# follow-up: for a GPU-eligible stack, this CPU recompute exists ONLY
# for ScopesPanel (the Viewer itself renders on the GPU directly, at
# native resolution, regardless of this array -- see
# _array_and_gpu_stack_for_source), so there's no reason it should ever
# touch native resolution at all, no matter how expensive the CPU-side
# math for some stage in the stack happens to be. Confirmed directly why
# this matters, not assumed: colour.LUT3D.apply() (a 'lut' stage's CPU
# path) measured 0.5-3.3 SECONDS at native/half preview resolutions
# (1920x1080 to 3840x2160) -- multiple SECONDS of UI-thread stall on
# every single edit tick for a stack containing a cube, even though the
# Viewer itself was already fast via GPU Stage 4. Scopes (histogram/
# waveform/vectorscope) are inherently statistical displays -- a few
# hundred thousand representative pixels is plenty, matching how a
# strided decimation already works for EDIT_DOWNRES_FACTOR above, just a
# fixed target SIZE (robust across wildly different source resolutions)
# rather than a fixed divisor.
SCOPES_MAX_DIMENSION = 512
# How long a stream of Stack edits has to go quiet before the preview
# snaps back to a full-resolution render -- short enough to feel
# immediate once you stop dragging, same spirit as the debounce already
# proven out for undo-history coalescing (main_window._UNDO_DEBOUNCE_MS)
# and the old resize-reload timer this replaces, just its own timer/value
# since "when to stop showing a reduced-quality preview" and "when to
# commit an undo step" are related but genuinely separate questions.
EDIT_SETTLE_DEBOUNCE_MS = 150

# Scrub-time downres (per Tom: "make scrub drop the render resolution to
# half until settled so its more performant") -- same settle-timer/burst-
# detection SHAPE as EDIT_SETTLE_DEBOUNCE_MS/_editing_settle_timer above,
# applied to the transport scrub bar instead of Stack edits. The scrub
# bar's own valueChanged fires continuously while dragging (QSlider's
# default tracking=True), so an ISOLATED single valueChanged (a plain
# click-to-jump) still decodes at full/normal resolution immediately --
# only a genuine BURST of rapid calls (an actual drag) gets temporarily
# downres'd, with one final full-quality decode once the burst goes
# quiet. See _on_video_scrub_slider_changed/_on_scrub_settled. Applies to
# BOTH scrub modes ("globally"): SOURCE (seek_video_frame, which decodes
# at full native resolution when settled -- currently NO cap at all, the
# main cost this targets) and RECORD (TimelineController.
# seek_to_record_frame, which already always caps to DECODE_PROXY_MAX_
# DIMENSION even when settled -- see that module's own docstring on why
# Timeline clips have no full-native "parked" mode; this only adds a
# temporary EXTRA halving of that existing cap during a burst).
SCRUB_DOWNRES_FACTOR = 2
SCRUB_SETTLE_DEBOUNCE_MS = 150

# The three things either side of the wipe (or the single view, when the
# wipe's disabled -- see _render_wipe, which reuses the Right selection as
# the single-view source rather than tracking a fourth independent state)
# can show -- see this module's docstring on Source vs Reference. "Source
# Before"/"Source After" are the pre-/post-stack states of the one Source
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
    ("Source Before", WIPE_SOURCE_SAMPLE_BEFORE),
    ("Source After", WIPE_SOURCE_SAMPLE_AFTER),
    ("Reference", WIPE_SOURCE_REFERENCE),
]


# Flat neutral level painted outside the key in a Highlight-mode preview
# (see _compose_highlight_overlay) -- per Tom: "solid gray for pixels
# outside of the key," replacing an earlier per-pixel luma-grayscale
# version (which still showed the ORIGINAL image's own brightness detail
# outside the key, competing with the colored key itself for attention
# instead of receding behind it, same as Resolve's own flat-gray
# Highlight background does).
HIGHLIGHT_GRAY_LEVEL = 0.2


def _compose_highlight_overlay(graded_array, matte):
    """MATTE HIGHLIGHT (per Tom: "a way to preview the mask similar to
    resolve's highlight mode") -- `graded_array` (H,W,3) where `matte`
    (H,W, same convention every matte-producing stage kind already
    returns -- see stack.py's docstring, "MATTE CHANNELS") is high, a
    FLAT HIGHLIGHT_GRAY_LEVEL gray (not the original image's own
    brightness) where it's low, smoothly blended in between by the
    matte's own soft edge. Pure numpy, no Qt -- called from
    _array_and_gpu_stack_for_source's own "MATTE HIGHLIGHT" branch, kept
    as its own free function so it's directly unit-testable without
    constructing a real SampleImagePanel."""
    gray = np.full_like(graded_array, HIGHLIGHT_GRAY_LEVEL)
    m = matte[..., np.newaxis]
    return graded_array * m + gray * (1.0 - m)


class SampleImagePanel(QWidget):
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
        viewer_mode_layout.setContentsMargins(2, 2, 2, 2)
        viewer_mode_layout.setSpacing(2)

        self._viewer_mode_badge = QToolButton()
        self._viewer_mode_badge.setObjectName("viewerModeBadge")
        self._viewer_mode_badge.setCheckable(True)
        self._viewer_mode_badge.setText("SOURCE")
        self._viewer_mode_badge.setToolTip(
            "Switch between SOURCE (the single loaded/selected clip) and "
            "RECORD (the Timeline's own composited, multi-clip playback) "
            "-- Play/Pause/Loop/Speed below always act on whichever one "
            "this is currently set to.")
        self._viewer_mode_badge.toggled.connect(self._on_viewer_mode_toggled)
        viewer_mode_layout.addWidget(self._viewer_mode_badge, alignment=Qt.AlignmentFlag.AlignLeft)

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

    def _active_transport_controller(self):
        """Whichever controller the transport bar's Play/Pause/Loop/
        Speed act on right now -- self._playback_controller (SOURCE, the
        single directly-loaded/selected clip) or self._timeline_
        controller (RECORD, the Timeline's own composited playback),
        picked by the SOURCE/RECORD toggle button, not anything
        automatic. Both expose the identical is_playing/play/pause/
        toggle/set_loop_enabled/set_speed/playback_started/
        playback_stopped surface on purpose, so every transport handler
        below can stay a single call through this method rather than an
        if/else per action."""
        return self._playback_controller if self._viewer_mode == "source" else self._timeline_controller

    def _build_video_transport_bar(self, layout):
        """Play/Pause, step-back/step-forward, in/out points + loop,
        speed, and a scrub slider -- a thin bar below the Viewer, visible
        while a video is loaded OR the Timeline has clips (see
        refresh_video_transport_visibility, load_video/_close_video_
        source, and main_window.py's own TimelinePanel.timeline_changed
        wiring). Deliberately colocated here in sample_image_panel.py
        rather than a new panel file -- same "own the state, own the UI
        that changes it" shape every other control in this panel already
        follows.

        TRANSPORT / SCRUB SPLIT -- originally (Tom, 2026-08-24) "the
        transport can be exclusive to the timeline BUT there should be a
        scrub bar only that's exclusive to the clip," later widened by
        the SOURCE/RECORD toggle (see _active_transport_controller):
        Play/Pause/Loop/Speed now drive WHICHEVER of self._playback_
        controller (SOURCE)/self._timeline_controller (RECORD) the
        toggle button is currently set to, not always the Timeline.
        Step/Set In/Set Out/Clear In-Out are still disabled outright
        (see _on_video_step_back_clicked's own docstring on why, and
        what's deferred -- that part of the original split is
        unchanged). The scrub slider below always drives self.
        _playback_controller/self._video_source regardless of the
        current toggle state, same as before the toggle existed.

        Keyboard shortcuts (Space/Left/Right/I/O/Home/End) are wired
        separately in _build_video_keyboard_shortcuts, called once from
        __init__ (not per-transport-bar-build, since shortcuts attach to
        `self`, not this bar's own widgets) -- Left/Right/I/O/Home/End
        are effectively inert now too, routed to the same disabled
        handlers as their matching buttons."""
        self._video_transport_widget = QWidget()
        outer_layout = QVBoxLayout(self._video_transport_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(2)

        # Scrub bar gets its OWN row, full width (per direct feedback:
        # squeezed inline next to a row of fixed-size buttons/combos, it
        # was "vanishingly small next to the other controls" -- it was
        # the only stretch=1 widget sharing space with everything else's
        # fixed minimum width). Buttons/loop/speed/timecode all move to
        # a second row below instead.
        scrub_row_layout = QHBoxLayout()
        scrub_row_layout.setContentsMargins(0, 0, 0, 0)
        self._video_scrub_slider = VideoScrubSlider(Qt.Orientation.Horizontal)
        self._video_scrub_slider.setMinimum(0)
        self._video_scrub_slider.valueChanged.connect(self._on_video_scrub_slider_changed)
        scrub_row_layout.addWidget(self._video_scrub_slider, stretch=1)
        outer_layout.addLayout(scrub_row_layout)

        transport_layout = QHBoxLayout()
        transport_layout.setContentsMargins(0, 0, 0, 0)
        transport_layout.setSpacing(2)  # within a group -- see addSpacing(8) BETWEEN groups below
        outer_layout.addLayout(transport_layout)

        self._video_play_button = QToolButton()
        self._video_play_button.setObjectName("videoTransportButton")
        self._video_play_button.setText("▶")
        self._video_play_button.setToolTip("Play/Pause (Space)")
        self._video_play_button.clicked.connect(self._on_video_play_pause_clicked)
        transport_layout.addWidget(self._video_play_button)

        # Step is still disabled (Tom, 2026-08-24: the transport is
        # exclusive to the Timeline) -- see _on_video_step_back_clicked's
        # own docstring on why frame-stepping specifically can't just be
        # left pointed at the single-clip controller (it would silently
        # hijack the Viewer away from whatever's currently on screen in
        # RECORD mode). Left in place, greyed out, rather than removed
        # outright -- a real Timeline-aware version is a scoped
        # fast-follow, not a dead feature. Set In/Set Out/Clear below
        # were disabled here too for the same original reason, but
        # re-enabled per Tom's later "marking in and out dont seem to
        # work at all" -- unlike Step, they always act on the single
        # directly-loaded/selected clip's own _playback_controller
        # regardless of the current SOURCE/RECORD toggle (see their own
        # handlers' docstrings), so there's no "which controller do I
        # hijack" ambiguity for them the way there is for Step.
        self._video_step_back_button = QToolButton()
        self._video_step_back_button.setObjectName("videoTransportButton")
        self._video_step_back_button.setText("◀│")
        self._video_step_back_button.setToolTip("Not yet supported for Timeline playback")
        self._video_step_back_button.setEnabled(False)
        self._video_step_back_button.clicked.connect(self._on_video_step_back_clicked)
        transport_layout.addWidget(self._video_step_back_button)

        self._video_step_forward_button = QToolButton()
        self._video_step_forward_button.setObjectName("videoTransportButton")
        self._video_step_forward_button.setText("│▶")
        self._video_step_forward_button.setToolTip("Not yet supported for Timeline playback")
        self._video_step_forward_button.setEnabled(False)
        self._video_step_forward_button.clicked.connect(self._on_video_step_forward_clicked)
        transport_layout.addWidget(self._video_step_forward_button)

        transport_layout.addWidget(self._build_video_transport_separator())
        transport_layout.addSpacing(4)

        self._video_set_in_button = QToolButton()
        self._video_set_in_button.setObjectName("videoTransportButton")
        self._video_set_in_button.setText("[")
        self._video_set_in_button.setToolTip("Set In point at the current frame (I)")
        self._video_set_in_button.clicked.connect(self._on_video_set_in_clicked)
        transport_layout.addWidget(self._video_set_in_button)

        self._video_set_out_button = QToolButton()
        self._video_set_out_button.setObjectName("videoTransportButton")
        self._video_set_out_button.setText("]")
        self._video_set_out_button.setToolTip("Set Out point at the current frame (O)")
        self._video_set_out_button.clicked.connect(self._on_video_set_out_clicked)
        transport_layout.addWidget(self._video_set_out_button)

        self._video_clear_in_out_button = QToolButton()
        self._video_clear_in_out_button.setObjectName("videoTransportButton")
        self._video_clear_in_out_button.setText("⊘")
        self._video_clear_in_out_button.setToolTip("Clear In/Out (reset to the full clip)")
        self._video_clear_in_out_button.clicked.connect(self._on_video_clear_in_out_clicked)
        transport_layout.addWidget(self._video_clear_in_out_button)

        # Add to Sequence (per Tom: "will be necessary to be able to
        # mark desired ranges and add to sequence") -- appends a NEW
        # Timeline clip covering exactly the marked [In, Out] range of
        # the single directly-loaded/selected clip (the full clip if
        # nothing's been marked -- see get_source_range_for_timeline's
        # own docstring), via add_source_range_to_timeline_requested
        # (wired in main_window.py to TimelinePanel.add_clip_with_range,
        # the same "this panel has no idea TimelinePanel exists"
        # arrangement every other cross-panel signal in this app uses).
        self._video_add_to_sequence_button = QToolButton()
        self._video_add_to_sequence_button.setObjectName("videoTransportButton")
        self._video_add_to_sequence_button.setText("➕")
        self._video_add_to_sequence_button.setToolTip(
            "Add the marked In/Out range (or the whole clip, if nothing's "
            "marked) to the Sequence as a new Timeline clip")
        self._video_add_to_sequence_button.clicked.connect(self._on_video_add_to_sequence_clicked)
        transport_layout.addWidget(self._video_add_to_sequence_button)

        self._video_loop_button = QToolButton()
        self._video_loop_button.setObjectName("videoTransportButton")
        self._video_loop_button.setText("⟲")
        self._video_loop_button.setToolTip("Loop playback -- the clip in SOURCE mode, the Timeline in RECORD mode")
        self._video_loop_button.setCheckable(True)
        self._video_loop_button.toggled.connect(self._on_video_loop_toggled)
        transport_layout.addWidget(self._video_loop_button)

        transport_layout.addWidget(self._build_video_transport_separator())
        transport_layout.addSpacing(4)

        self._video_speed_combo = QComboBox()
        for preset in video_playback_controller.SPEED_PRESETS:
            self._video_speed_combo.addItem(f"{preset:g}x", preset)
        self._video_speed_combo.setCurrentIndex(
            video_playback_controller.SPEED_PRESETS.index(video_playback_controller.DEFAULT_SPEED))
        self._video_speed_combo.currentIndexChanged.connect(self._on_video_speed_changed)
        transport_layout.addWidget(self._video_speed_combo)

        # Two timecode fields (per direct feedback: "there are 3
        # timecode fields ... you can make it 2 only" -- collapses the
        # single-clip Source readout and the Timeline's own combined
        # "Src ... Rec ..." readout into one unified Source field plus a
        # standing Record field):
        #
        #   - _source_timecode_label: whichever "source" is actually
        #     ACTIVE right now -- the single directly-loaded/selected
        #     clip's own position in SOURCE mode (self._video_source),
        #     or the Timeline's CURRENT clip's own source position in
        #     RECORD mode (TimelineController.current_source_frame --
        #     "the clip under the playhead"), never both at once. See
        #     _update_source_timecode_label, the single place that
        #     decides which.
        #   - _record_timecode_label: the Timeline's own position
        #     (TimelineController.current_record_frame) -- unlike the
        #     Source field, this ALWAYS reflects the whole Timeline
        #     regardless of which mode the toggle is on, since "Record"
        #     is unambiguous (there's only ever one Timeline). Blank
        #     whenever the Timeline has no clips at all.
        #
        # Both expressed at the Timeline's own frame_rate when showing a
        # Timeline-sourced position (see timeline_playback_controller
        # .py's current_source_frame/current_record_frame docstrings on
        # why mixed-fps-aware timecode isn't attempted here -- explicitly
        # out of scope for this pass); the single-clip Source case uses
        # that clip's own frame_rate, same as before this merge.
        self._source_timecode_label = QLabel("")
        self._source_timecode_label.setObjectName("videoTimecodeLabel")
        self._source_timecode_label.setToolTip(
            "Position within whichever source is currently active: the "
            "single loaded/selected clip (SOURCE mode) or the Timeline's "
            "current clip under the playhead (RECORD mode)")
        transport_layout.addSpacing(4)
        transport_layout.addWidget(self._source_timecode_label)

        self._record_timecode_label = QLabel("")
        self._record_timecode_label.setObjectName("videoTimecodeLabel")
        self._record_timecode_label.setToolTip("Position within the whole Timeline")
        transport_layout.addSpacing(8)
        transport_layout.addWidget(self._record_timecode_label)

        self._video_transport_widget.setVisible(False)  # shown once a video is loaded OR the Timeline has clips
        layout.addWidget(self._video_transport_widget)

    @staticmethod
    def _build_video_transport_separator():
        """A thin vertical divider between logical control groups (Phase
        4 follow-up, per Tom: transport bar felt like "one long row" --
        groups it into play/step, in/out/loop, scrub, and speed/timecode
        clusters instead)."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        return separator

    def _build_video_keyboard_shortcuts(self):
        """Space/Left/Right/I/O/Home/End (Phase 4 transport upgrade, per
        Tom's own confirmed scope) -- attached to `self` (this whole
        panel) with the default WidgetWithChildrenShortcut context, so
        they fire whenever focus is anywhere inside this panel (the
        Viewer/transport bar), not globally across the app and not only
        when one specific sub-widget has focus. All are permanently
        active (not conditionally enabled only while a video is loaded)
        -- each handler already routes through methods that are no-ops
        with no video open (_playback_controller.toggle/pause,
        seek_video_frame), so there's nothing to gate here."""
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._on_video_play_pause_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._on_video_step_back_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._on_video_step_forward_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self._on_video_set_in_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self._on_video_set_out_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, self._on_video_jump_to_in_clicked)
        QShortcut(QKeySequence(Qt.Key.Key_End), self, self._on_video_jump_to_out_clicked)

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

    def _recompute_working_array(self):
        """`_image_array` is always DERIVED from `_original_array`, never
        mutated in place -- was also where Adapt's colorspace conversion
        used to be applied before that feature was removed (per Tom:
        "remove the ability to adapt reference or source"); now a
        straight passthrough, kept as its own method rather than inlined
        at every call site so there's still one obvious place to look."""
        if self._original_array is None:
            self._image_array = None
            self._image_array_downres = None
            self._image_array_scopes = None
            return
        self._image_array = self._original_array
        self._image_array_downres = self._make_downres_array(self._image_array)
        self._image_array_scopes = self._make_scopes_array(self._image_array)
        self._image_array_generation += 1

    @staticmethod
    def _make_downres_array(array):
        """A cheap, strided decimation of `array` by EDIT_DOWNRES_FACTOR
        in each dimension -- deliberately nearest-neighbor (no
        interpolation cost at all), not a proper area-averaged resample:
        this is a temporary, throwaway, intentionally-lower-quality frame
        shown only while a burst of edits is actively in progress (see
        refresh_preview), not something anyone should be scrutinizing for
        quality -- spending real CPU time making the THROWAWAY frame look
        nicer would undercut the entire reason it exists. Floored at 1px
        per side so a very small source/panel can never decimate to a
        degenerate empty array."""
        factor = EDIT_DOWNRES_FACTOR
        decimated = array[::factor, ::factor]
        if decimated.shape[0] < 1 or decimated.shape[1] < 1:  # pragma: no cover -- only a 0px source could hit this
            return array
        return np.ascontiguousarray(decimated)

    @staticmethod
    def _make_scopes_array(array):
        """Same cheap strided-decimation approach as _make_downres_array,
        but targets a fixed long-edge SIZE (SCOPES_MAX_DIMENSION) rather
        than a fixed divisor -- robust across wildly different source
        resolutions (a 1080p source and an 8K source both end up around
        the same pixel count), which a fixed EDIT_DOWNRES_FACTOR-style
        divisor wouldn't guarantee. See SCOPES_MAX_DIMENSION's own
        comment for why this exists as a THIRD tier, separate from
        _image_array_downres."""
        h, w = array.shape[:2]
        long_edge = max(h, w)
        # ceil, not round -- round() can land on a factor that still
        # leaves the decimated long edge ABOVE SCOPES_MAX_DIMENSION
        # whenever the division's fractional part is under 0.5 (e.g.
        # 2200/512 = 4.30 rounds to 4, but a stride of 4 only gets a
        # 2200px edge down to 550px, still over the cap) -- ceil always
        # rounds the stride UP, guaranteeing the result stays at or
        # under the target.
        factor = max(1, math.ceil(long_edge / SCOPES_MAX_DIMENSION))
        decimated = array[::factor, ::factor]
        if decimated.shape[0] < 1 or decimated.shape[1] < 1:  # pragma: no cover -- only a 0px source could hit this
            return array
        return np.ascontiguousarray(decimated)

    def _recompute_reference_working_array(self):
        """Straight passthrough (see _recompute_working_array's own
        docstring on why this used to convert)."""
        if self._reference_original_array is None:
            self._reference_image_array = None
            return
        self._reference_image_array = self._reference_original_array

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

    def _close_video_source(self):
        if self._video_source is not None:
            # stop(), not pause() -- no point re-parking through a full
            # seek+decode just to immediately discard it (see
            # VideoPlaybackController.stop's own docstring); also avoids
            # pause() trying to decode from a source we're about to close.
            self._playback_controller.stop()
            self._video_source.close()
            self._video_source = None
            self._video_frame_index = 0
        self.refresh_video_transport_visibility()

    def load_video(self, path):
        """Opens `path` as a video source and loads its FIRST frame into
        the exact same grading pipeline every still-image Sample load
        already uses (see _load_video_frame_core) -- this is PARKED-FRAME
        review only, not realtime playback (see video_playback_
        controller.py for that, a fully separate, decoupled path per
        Tom's own confirmed scope: "realtime playback but no simultaneous
        grading/playback"). Returns True on success.

        A NO-OP (still returns True) if `path` is already the currently
        open source -- reopening the identical file would otherwise
        discard the user's own in/out marks, loop state, and parked
        scrub position for no reason (Tom: "marking in and out dont
        seem to work at all" -- one real cause, found while building Add
        to Sequence: selecting a just-added Timeline clip whose
        source_path is the SAME file it was just marked on re-triggers
        this method via main_window._on_timeline_clip_selected, which
        used to always clear those marks right back out). A genuinely
        DIFFERENT path still gets the full "fresh clip" reset just
        below, unchanged."""
        if self._video_source is not None and str(self._video_source.path) == str(path):
            return True
        try:
            source = video_source.VideoSource(path)
        except Exception as e:  # noqa: BLE001 -- surface any open/decode failure to the user
            QMessageBox.critical(self, "Failed to open video", f"{path}\n\n{e}")
            return False
        self._close_video_source()
        self._video_source = source
        # Fresh clip -- any in/out points or loop state left over from a
        # PREVIOUSLY loaded video are stale (Phase 4 transport upgrade),
        # not something a newly opened clip should inherit.
        self._playback_controller.clear_in_out()
        self._playback_controller.set_loop_enabled(False)
        self._video_loop_button.setChecked(False)  # keep the button in sync with the controller reset above
        ok = self._load_video_frame_core(0)
        if ok:
            # Only actually touches the scrub bar if SOURCE mode is
            # current (see _refresh_scrub_slider_for_mode's own
            # docstring) -- loading a new Source clip while parked in
            # RECORD mode must leave the Timeline-range scrub bar alone.
            self._refresh_scrub_slider_for_mode()
            self.refresh_video_transport_visibility()
            self.video_loaded.emit(source.frame_count, source.frame_rate)
        return ok

    def seek_video_frame(self, frame_index, downres=False):
        """Scrub/step to a specific frame of the currently-open video --
        the public entry point the transport UI (video_playback_
        controller.py / the scrub bar) calls. No-op (returns False) if no
        video is currently open. `downres`: see _load_video_frame_core's
        own docstring -- callers other than the scrub bar itself always
        want the default full-resolution decode."""
        if self._video_source is None:
            return False
        return self._load_video_frame_core(frame_index, downres=downres)

    def _load_video_frame_core(self, frame_index, downres=False):
        """The video equivalent of _load_image_core -- decodes ONE frame
        from self._video_source and feeds it through the EXACT SAME
        downstream pipeline (_recompute_working_array/
        _recompute_sample_after/_render_wipe) a still image load already
        uses, so grading a parked video frame works identically to
        grading a still -- no separate grading code path exists or is
        needed.

        `downres` (see SCRUB_DOWNRES_FACTOR/_on_video_scrub_slider_
        changed): decodes at half the video's own native resolution
        instead of full native -- only ever passed True mid-scrub-burst;
        every other caller (a plain click-to-jump, Step, Set In/Out,
        session restore) keeps the default full-native decode this
        method has always done."""
        max_dimension = None
        if downres:
            native_long_side = max(self._video_source.width, self._video_source.height)
            max_dimension = max(1, native_long_side // SCRUB_DOWNRES_FACTOR)
        try:
            array = self._video_source.seek_to_frame(frame_index, max_dimension=max_dimension)
        except Exception as e:  # noqa: BLE001 -- surface any decode failure to the user
            QMessageBox.critical(self, "Failed to decode video frame", str(e))
            return False
        if array is None:
            return False

        self._video_frame_index = frame_index
        self._current_path = self._video_source.path
        self._original_array = array
        self.status_label.setText("")
        self._recompute_working_array()
        self._recompute_sample_after()
        self._render_wipe()
        self._casting_controller.on_stack_or_image_changed()
        self.image_loaded.emit(array.shape[1], array.shape[0])
        self._set_video_transport_position(frame_index)
        return True

    def _set_video_transport_position(self, frame_index):
        """Updates the scrub slider + timecode label to reflect
        `frame_index` WITHOUT re-triggering a seek -- called after every
        successful parked-frame load (_load_video_frame_core). Scrub bar
        write gated to SOURCE mode, same reasoning as _on_video_frame_
        changed/_on_timeline_frame_changed's own gates -- a parked
        single-clip seek (e.g. session restore) while RECORD mode is
        active must not stomp the Timeline-range scrub bar."""
        if self._viewer_mode == "source":
            self._suppress_video_scrub_signal = True
            self._video_scrub_slider.setValue(frame_index)
            self._suppress_video_scrub_signal = False
        self._update_video_timecode_label(frame_index)

    _SOURCE_TIMECODE_TOOLTIP = (
        "Position within whichever source is currently active: the "
        "single loaded/selected clip (SOURCE mode) or the Timeline's "
        "current clip under the playhead (RECORD mode)")

    def _update_video_timecode_label(self, frame_index):
        """Real SMPTE timecode for the single directly-loaded/selected
        clip (Phase 4, replacing the old "N / total" label -- see
        timecode.py) -- frame count is kept as a secondary detail in the
        tooltip, not the primary display, per the plan's own scope note.

        Only actually writes to the shared _source_timecode_label while
        SOURCE mode is active (see that label's own construction-time
        docstring) -- RECORD mode's Source field comes from _on_
        timeline_frame_changed instead, and a background tick from the
        single-clip controller while it's the INACTIVE one must not
        overwrite that."""
        if self._viewer_mode != "source":
            return
        if self._video_source is None:
            self._source_timecode_label.setText("")
            self._source_timecode_label.setToolTip(self._SOURCE_TIMECODE_TOOLTIP)
            return
        tc = timecode.frames_to_timecode(frame_index, self._video_source.frame_rate)
        self._source_timecode_label.setText(tc)
        self._source_timecode_label.setToolTip(
            f"Frame {frame_index + 1} / {self._video_source.frame_count}")

    def _on_timeline_frame_changed(self):
        """TimelineController.frame_changed fired. Updates the RECORD
        field unconditionally -- the Timeline's own position is
        unambiguous regardless of which mode the toggle is on, per
        direct feedback: "one source [field] ... and the record
        timecode" (a standing field, not mode-gated the way Source is).
        Updates the shared _source_timecode_label too, but ONLY while
        RECORD mode is active -- the Timeline's CURRENT clip's own
        source position ("the clip under the playhead"); SOURCE mode's
        writes to that same field come from _update_video_timecode_label
        instead, gated the identical way in reverse, so exactly one of
        the two ever writes to it at a time.

        Also keeps the scrub bar's VALUE live during RECORD-mode
        playback/seeking (its RANGE is set separately, see
        _refresh_scrub_slider_for_mode) -- gated to RECORD only, so a
        stray tick while SOURCE mode is active can't overwrite the
        scrub bar's current SOURCE-range value with a position from the
        wrong numbering."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        source_frame = self._timeline_controller.current_source_frame()
        record_frame = self._timeline_controller.current_record_frame()
        if timeline_obj is None or record_frame is None:
            self._record_timecode_label.setText("")
            if self._viewer_mode == "record":
                self._source_timecode_label.setText("")
            return
        self._record_timecode_label.setText(
            timecode.frames_to_timecode(record_frame, timeline_obj.frame_rate))
        if self._viewer_mode == "record":
            if source_frame is not None:
                self._source_timecode_label.setText(
                    timecode.frames_to_timecode(source_frame, timeline_obj.frame_rate))
                self._source_timecode_label.setToolTip(self._SOURCE_TIMECODE_TOOLTIP)
            else:
                self._source_timecode_label.setText("")
            self._suppress_video_scrub_signal = True
            self._video_scrub_slider.setValue(record_frame)
            self._suppress_video_scrub_signal = False

    def _on_video_play_pause_clicked(self):
        self._active_transport_controller().toggle()
        self._update_video_play_button_text()

    def _update_video_play_button_text(self):
        self._video_play_button.setText("⏸" if self._active_transport_controller().is_playing else "▶")

    def activate_record_mode(self):
        """Public switch to RECORD mode, for a caller outside this panel
        (MainWindow, when a seek originates from the Timeline view
        itself -- see its own wiring of TimelinePanel.seek_requested).
        Clicking the Timeline's ruler should visibly put the Viewer into
        RECORD, not silently seek the Timeline underneath a still-SOURCE
        transport (scrub bar/labels gated to the active mode, see
        _on_timeline_frame_changed's own docstring) -- without this, the
        Timeline view's own playhead would move while the transport
        looked frozen, an actual reported "playhead desync." No-op if
        already in RECORD mode. Same pause-the-side-being-left-first
        reasoning as _on_viewer_mode_toggled, but only the SOURCE side --
        this is called immediately before a RECORD-mode seek, which
        pauses the Timeline controller itself already."""
        if self._viewer_mode == "record":
            return
        self._playback_controller.pause()
        self._set_viewer_mode("record")
        self._update_video_play_button_text()

    def _on_viewer_mode_toggled(self, checked):
        """The SOURCE/RECORD toggle button itself (per Tom's follow-up:
        "it should be a toggle you can control all the time" -- not
        something play/pause flips automatically anymore, see this
        panel's own docstring on the toggle button). Pauses BOTH
        transport controllers before switching -- there's only one
        Viewer display (self.compare), so if the one you're leaving was
        mid-playback it must stop before the other can safely start;
        without this, switching mid-playback would leave two playback
        loops both trying to push frames to the same surface."""
        self._playback_controller.pause()
        self._timeline_controller.pause()
        self._set_viewer_mode("record" if checked else "source")
        self._update_video_play_button_text()

    def _set_viewer_mode(self, mode):
        """"source" or "record" -- see _active_transport_controller/
        _on_viewer_mode_toggled and style.py's QFrame#viewerModeFrame/
        #viewerModeBadge rules. Purely a manual switch (this panel's own
        toggle button, see _on_viewer_mode_toggled) -- NOT driven by
        play/pause state; SOURCE and RECORD are equally valid states to
        be sitting in while paused OR while playing.

        The badge button's own RECORD/SOURCE look comes from Qt's
        native :checked pseudo-state (style.py, same convention the
        Loop transport button already uses) -- setChecked() below
        repaints it with no unpolish/polish dance needed. The wrapping
        frame isn't a button (no native checked state to hook), so it
        still goes through the plain custom-property + repolish
        convention every other dynamic-property QSS rule in this app
        uses (see stack_panel.py's StackStageRow.set_selected)."""
        if mode == self._viewer_mode:
            return
        self._viewer_mode = mode
        self._viewer_mode_badge.setText("RECORD" if mode == "record" else "SOURCE")
        self._viewer_mode_badge.blockSignals(True)
        self._viewer_mode_badge.setChecked(mode == "record")
        self._viewer_mode_badge.blockSignals(False)
        self._viewer_mode_frame.setProperty("viewerMode", mode)
        self._viewer_mode_frame.style().unpolish(self._viewer_mode_frame)
        self._viewer_mode_frame.style().polish(self._viewer_mode_frame)
        self._refresh_scrub_slider_for_mode()
        self._refresh_source_timecode_for_mode()

    def _refresh_source_timecode_for_mode(self):
        """Immediately recomputes the shared Source timecode field to
        match self._viewer_mode -- called on every mode switch, same
        "don't wait for the next tick" reasoning as _refresh_scrub_
        slider_for_mode. Without this, switching modes while PAUSED
        would leave the field showing whatever the PREVIOUSLY active
        mode's own last tick wrote, not the mode just switched to (the
        Record field needs no equivalent -- it's mode-independent and
        already kept live by every _on_timeline_frame_changed call)."""
        if self._viewer_mode == "source":
            self._update_video_timecode_label(self._video_frame_index)
        else:
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            source_frame = self._timeline_controller.current_source_frame()
            if timeline_obj is not None and source_frame is not None:
                self._source_timecode_label.setText(
                    timecode.frames_to_timecode(source_frame, timeline_obj.frame_rate))
            else:
                self._source_timecode_label.setText("")
            self._source_timecode_label.setToolTip(self._SOURCE_TIMECODE_TOOLTIP)

    def _refresh_scrub_slider_for_mode(self):
        """Recomputes the scrub bar's range AND position to match
        self._viewer_mode -- SOURCE shows the single directly-loaded/
        selected clip's own frame range (self._video_source), RECORD
        shows the WHOLE Timeline's (timeline.Timeline.total_frame_count
        -- per direct feedback: "the scrub bar seems to only represent a
        single clip in the timeline. it should represent the whole
        timeline"). Called on every mode switch (_set_viewer_mode) and
        whenever the underlying thing that mode's range depends on
        changes (refresh_video_transport_visibility -- a new Source
        video loaded/closed, or the Timeline's own clip list changing,
        see that method's own docstring on both triggers) -- NOT on
        every playback tick, see _on_video_frame_changed/_on_timeline_
        frame_changed for the per-tick position updates that keep the
        already-set range's VALUE live during actual playback.

        No Timeline-level in/out sub-range exists yet (see this
        module's own docstring and timeline_playback_controller.py's),
        so RECORD mode always clears the scrub bar's in/out markers --
        SOURCE mode still shows the single clip's own, via the existing
        _sync_video_in_out_markers."""
        self._suppress_video_scrub_signal = True
        if self._viewer_mode == "record":
            timeline_obj = self._timeline_getter() if self._timeline_getter else None
            total = timeline_obj.total_frame_count() if timeline_obj else 0
            self._video_scrub_slider.setMaximum(max(0, total - 1))
            record_frame = self._timeline_controller.current_record_frame()
            self._video_scrub_slider.setValue(record_frame if record_frame is not None else 0)
            self._video_scrub_slider.set_in_out(None, None)
        else:
            source = self._video_source
            self._video_scrub_slider.setMaximum(max(0, source.frame_count - 1) if source else 0)
            self._video_scrub_slider.setValue(self._video_frame_index if source else 0)
            self._sync_video_in_out_markers()
        self._suppress_video_scrub_signal = False

    def _on_video_step_back_clicked(self):
        """Disabled for now (button greyed out, see _build_video_transport
        _bar) -- frame-stepping is scoped to the single directly-loaded
        clip's OWN seek_video_frame, which would silently hijack the
        Viewer away from whatever the Timeline is currently showing (see
        module docstring on the transport/scrub split). A real Timeline-
        aware step (including backward across a clip boundary) is a
        fast-follow, not built in this pass -- see the approved Timeline
        plan's own scope note."""
        return

    def _on_video_step_forward_clicked(self):
        """Disabled for now -- see _on_video_step_back_clicked's own
        docstring."""
        return

    def _on_video_scrub_slider_changed(self, value):
        """Mode-aware (per direct feedback: "the scrub bar seems to only
        represent a single clip in the timeline. it should represent
        the whole timeline") -- SOURCE seeks the single directly-loaded
        clip (unchanged from before the SOURCE/RECORD toggle existed);
        RECORD seeks the whole Timeline via TimelineController.
        seek_to_record_frame, which pauses it first the same way
        _playback_controller.pause() does here for the SOURCE case.

        SCRUB-TIME DOWNRES (see SCRUB_DOWNRES_FACTOR) -- _scrub_settle_
        timer.isActive() being True means a PREVIOUS call to this method
        started it and it hasn't fired yet, i.e. this is at least the
        second tick of an ongoing drag, not an isolated click-to-jump;
        only that case decodes downres'd. Restarting the timer on every
        call (regardless of which branch) is what turns "quiet for
        SCRUB_SETTLE_DEBOUNCE_MS" into the signal that dragging has
        stopped -- see _on_scrub_settled."""
        if self._suppress_video_scrub_signal:
            return
        scrub_in_progress = self._scrub_settle_timer.isActive()
        self._scrub_settle_timer.start(SCRUB_SETTLE_DEBOUNCE_MS)
        self._last_scrub_frame_index = value
        self._last_scrub_was_downres = scrub_in_progress
        if self._viewer_mode == "record":
            self._timeline_controller.seek_to_record_frame(value, downres=scrub_in_progress)
        else:
            self._playback_controller.pause()
            self.seek_video_frame(value, downres=scrub_in_progress)

    def _on_scrub_settled(self):
        """Fires once a burst of scrub ticks has gone quiet for SCRUB_
        SETTLE_DEBOUNCE_MS. If the most recent tick was downres'd,
        replace it with one final normal-quality decode at the same
        frame now that dragging has stopped -- a no-op if it wasn't (an
        isolated click, or nothing scrubbed yet this session)."""
        if not self._last_scrub_was_downres or self._last_scrub_frame_index is None:
            return
        frame_index = self._last_scrub_frame_index
        self._last_scrub_was_downres = False
        if self._viewer_mode == "record":
            self._timeline_controller.seek_to_record_frame(frame_index, downres=False)
        else:
            self.seek_video_frame(frame_index, downres=False)

    def _on_video_frame_changed(self, frame_index):
        # Realtime playback ticking -- update the slider/timecode WITHOUT
        # touching self._video_frame_index (that stays at the last
        # PARKED position until pause re-syncs it -- see
        # VideoPlaybackController.pause's own docstring). Scrub bar
        # write gated to SOURCE mode, same reasoning as _on_timeline_
        # frame_changed's own RECORD-only gate -- its RANGE belongs to
        # whichever mode is active (see _refresh_scrub_slider_for_mode),
        # so a stray SOURCE tick while RECORD is active must not stomp
        # the Timeline-range value with a single-clip frame index.
        if self._viewer_mode == "source":
            self._suppress_video_scrub_signal = True
            self._video_scrub_slider.setValue(frame_index)
            self._suppress_video_scrub_signal = False
        self._update_video_timecode_label(frame_index)

    def _on_video_playback_stopped(self):
        self._update_video_play_button_text()

    def _sync_video_in_out_markers(self):
        """Pushes _playback_controller's own in_point/out_point onto the
        scrub slider's visual markers (Phase 4 follow-up, per Tom: "hard
        to tell where the marks are") -- called after every action that
        can change either bound, so the slider never shows stale marks.
        Only ever called for SOURCE mode's own range (see
        _refresh_scrub_slider_for_mode) -- RECORD mode has no in/out
        sub-range concept yet, so it clears the markers instead of
        calling this."""
        self._video_scrub_slider.set_in_out(
            self._playback_controller.in_point, self._playback_controller.out_point)

    def _on_video_set_in_clicked(self):
        """Marks In at the single directly-loaded/selected clip's
        CURRENT frame (per Tom: "marking in and out dont seem to work
        at all") -- always acts on _playback_controller regardless of
        the current SOURCE/RECORD toggle (see this method's sibling
        handlers and the "..." menu's own reasoning), since a marked
        range only ever exists to be added to the Sequence via Add to
        Sequence (_on_video_add_to_sequence_clicked), a single-clip
        concept. Timeline-level in/out (a partial-range TRIM of a clip
        ALREADY on the Timeline) is a different, still-unbuilt feature
        -- see timeline_playback_controller.py's own module docstring."""
        self._playback_controller.set_in_point()
        self._on_video_in_out_changed()

    def _on_video_set_out_clicked(self):
        """See _on_video_set_in_clicked's own docstring."""
        self._playback_controller.set_out_point()
        self._on_video_in_out_changed()

    def _on_video_clear_in_out_clicked(self):
        """See _on_video_set_in_clicked's own docstring."""
        self._playback_controller.clear_in_out()
        self._on_video_in_out_changed()

    def _on_video_in_out_changed(self):
        """Shared tail for Set In/Set Out/Clear -- only actually repaints
        the scrub bar's markers while SOURCE mode is active (see
        _sync_video_in_out_markers/_refresh_scrub_slider_for_mode's own
        docstrings on why RECORD mode's scrub bar has no in/out marker
        concept at all); _playback_controller's own in_point/out_point
        are updated either way, regardless of mode, so a mark made while
        parked in RECORD mode is still there once you switch back."""
        if self._viewer_mode == "source":
            self._sync_video_in_out_markers()

    def _on_video_loop_toggled(self, checked):
        self._active_transport_controller().set_loop_enabled(checked)

    def _on_video_speed_changed(self, index):
        speed = self._video_speed_combo.itemData(index)
        if speed is not None:
            self._active_transport_controller().set_speed(speed)

    def _on_video_jump_to_in_clicked(self):
        """Home -- jumps to the SAME in_point Set In/Out controls (see
        _on_video_set_in_clicked). Pauses first, same "an explicit jump
        always parks, never leaves the timer ticking through it"
        convention the scrub bar's own drag-to-seek already uses."""
        if self._video_source is None:
            return
        self._playback_controller.pause()
        self.seek_video_frame(self._playback_controller.in_point)

    def _on_video_jump_to_out_clicked(self):
        """End -- jumps to the EFFECTIVE out point (out_point if
        explicitly set, else the clip's own last frame -- same
        "None means play to the end" convention VideoPlaybackController.
        _out_limit already uses internally for playback itself)."""
        source = self._video_source
        if source is None:
            return
        out_point = self._playback_controller.out_point
        if out_point is None:
            out_point = max(0, source.frame_count - 1) if source.frame_count else 0
        self._playback_controller.pause()
        self.seek_video_frame(out_point)

    def get_source_range_for_timeline(self):
        """(name, source_path, media_kind, in_frame, out_frame) for the
        single directly-loaded/selected clip's currently MARKED range
        (per Tom: "will be necessary to be able to mark desired ranges
        and add to sequence") -- out_frame falls back to the clip's own
        last frame when nothing's been marked (_playback_controller.
        out_point is None, its own "play to the end" convention), so
        this doubles as "the whole clip" when no range was ever marked,
        same default add_clip_from_library_entry already uses. None if
        no video is currently loaded as Source -- nothing to add."""
        source = self._video_source
        if source is None:
            return None
        in_frame = self._playback_controller.in_point
        out_frame = self._playback_controller.out_point
        if out_frame is None:
            out_frame = max(0, source.frame_count - 1) if source.frame_count else in_frame
        return Path(source.path).stem, source.path, "video", in_frame, out_frame

    def _on_video_add_to_sequence_clicked(self):
        range_info = self.get_source_range_for_timeline()
        if range_info is None:
            return
        self.add_source_range_to_timeline_requested.emit(*range_info)

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

    def refresh_video_transport_visibility(self):
        """Shows the transport bar whenever there's ANYTHING for it to
        play: a directly-loaded video (self._video_source, SOURCE mode's
        own domain) OR the Timeline has at least one clip (RECORD mode's
        domain, see _active_transport_controller). Called from load_
        video/_close_video_source (single-clip load/unload) and wired in
        main_window.py to TimelinePanel.timeline_changed (Timeline add/
        remove/clear/load) -- either source of change needs to
        re-evaluate this independently, since a Timeline built without
        ever loading a single video directly must still make the
        transport visible.

        Also the hook point for keeping RECORD mode's scrub-bar RANGE
        (per direct feedback: "the scrub bar seems to only represent a
        single clip in the timeline. it should represent the whole
        timeline") and the Source timecode field both in sync with the
        Timeline's own clip list -- a clip added/removed/reordered
        changes total_frame_count and possibly what's under the
        playhead, and this is already called on every such change via
        the timeline_changed wiring above, so there's no need for a
        second, separate hook."""
        timeline_obj = self._timeline_getter() if self._timeline_getter else None
        has_timeline_clips = bool(timeline_obj and timeline_obj.clips)
        self._video_transport_widget.setVisible(self._video_source is not None or has_timeline_clips)
        self._refresh_scrub_slider_for_mode()
        self._refresh_source_timecode_for_mode()

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

    def pick_pixel_color_for_stage(self, stage_id, callback):
        """Eyedropper entry point (per Tom) -- arms self.compare.
        arm_eyedropper (see that method's own docstring) for a click
        ANYWHERE on the Viewer, then resolves the click's own fractional
        position against `stage_id`'s ACTUAL INPUT, not whatever's on
        screen (per Tom: "the color picker for the key still seems to
        rely on actually pulling a pixel from the preview image, it
        should be pulling up the pixel address of the input of the key
        entry" -- the on-screen pixel reflects the FULL composited stack,
        including every stage AFTER stage_id, any active wipe-compare
        source selection, and any Highlight overlay, none of which is
        what stage_id itself actually reads). Reads self._image_array
        (the raw, full-resolution, pre-stack source -- see this module's
        own docstring) at the picked pixel, folds only the stages
        strictly BEFORE stage_id over that ONE pixel (see stack.
        running_array_before_stage's own docstring for why a single
        pixel is exactly as correct, and far cheaper, than re-grading
        the whole image), and hands the result to `callback(rgb)` as a
        plain (r, g, b) float tuple -- still in whatever encoding the
        working pipeline is in at that point (e.g. ACEScct), same as
        keyer.py's own OKLCh decode already expects. `callback` is
        simply never invoked if anything along the way doesn't resolve
        (no source image loaded, no live stack, the click landed outside
        the image, or stage_id can't be resolved -- see running_array_
        before_stage's own docstring for that last case) -- same silent-
        no-op convention every other matte/stage reference in this app
        already uses. StackPanel doesn't hold a WipeCompareWidget
        reference of its own -- panels only ever talk via getters/
        signals through main_window.py, same as set_stack_getter/
        set_highlight_getter above -- so it reaches the Viewer through
        THIS panel instead, via StackPanel.set_eyedropper_requester."""
        def _on_click_fraction(x_frac, y_frac):
            if self._image_array is None:
                return
            stack = self._stack_getter() if self._stack_getter else None
            if stack is None:
                return
            h, w = self._image_array.shape[:2]
            x = min(w - 1, max(0, int(x_frac * w)))
            y = min(h - 1, max(0, int(y_frac * h)))
            pixel = self._image_array[y:y + 1, x:x + 1, :]
            result = running_array_before_stage(stack, pixel, stage_id)
            if result is None:
                return
            callback(tuple(float(v) for v in result[0, 0]))
        self.compare.arm_eyedropper(_on_click_fraction)

    def refresh_preview(self):
        """Public trigger for 'something this panel doesn't own directly
        changed, re-render' -- used for StackPanel.stack_changed, since
        that fires on every stack mutation and this panel has no other
        way to know. Recomputes Sample After specifically (the only one
        of the three wipe sources that depends on the Stack) and always
        re-renders the wipe regardless of which source(s) are currently
        selected -- if Sample After isn't currently showing on either
        side, this is a cheap no-op render, not a wrong one.

        EDITING-TIME DOWNRES (2026-08-09, replaces the old zoom/resize-
        reload band-aid -- see this module's "PREVIEW RESOLUTION"
        comment): `_editing_settle_timer.isActive()` being True here means
        a PREVIOUS call to this method started it and it hasn't fired yet
        -- i.e. this is at least the second edit of an ongoing burst
        (a slider drag firing stack_changed on every tick), not an
        isolated single edit. Only THAT case renders from the downres
        array; a genuinely isolated edit (the common case: toggling a
        stage, picking a colorspace, adding/removing a stage) always
        renders at full quality immediately, exactly like before this
        existed -- there's no burst to protect against, so there's
        nothing to gain from degrading it, only a pointless soft frame
        followed by a redundant re-render once the settle timer fires.
        Restarting the timer on every call (regardless of which branch
        rendered) is what turns "quiet for EDIT_SETTLE_DEBOUNCE_MS" into
        the signal that a burst has ended -- see _on_editing_settled.

        GPU-ELIGIBLE STACKS ALWAYS RECOMPUTE AT SCOPES RESOLUTION
        (2026-08-09, follow-up REVISED 2026-08-10): for a stack
        gpu_stack_renderer.is_stack_gpu_eligible approves, the Viewer
        grades it live on the GPU itself (see _render_wipe/
        _array_and_gpu_stack_for_source) -- it never reads
        _current_after_array at all. The ONLY consumer of this recompute
        for such a stack is ScopesPanel (via after_image_changed), so it
        never needs native (or even EDIT_DOWNRES_FACTOR-halved)
        resolution -- it always runs against _image_array_scopes (see
        SCOPES_MAX_DIMENSION's own comment), REGARDLESS of burst-vs-
        isolated state, not just during a burst. The original 2026-08-09
        version of this fix only skipped the recompute DURING a burst,
        still paying full native-resolution cost on the first tick of
        every edit and again once a burst settled -- which sounded right
        until a real, measured case broke it: a 'lut' stage's CPU path
        (colour.LUT3D.apply()) costs 0.5-3.3 SECONDS at native/half
        preview resolution, so EVERY isolated edit (not just bursts) was
        still stalling the UI thread for seconds at a time on a stack
        with a cube in it. Recomputing at a small fixed SIZE unconditionally
        removes every one of those stall points at once, and needs no
        "catch up once settled" step afterward (see _on_editing_settled)
        since there's no higher-resolution version of THIS array to
        catch up to -- Scopes simply always looks at the same small,
        fast, representative sample, no staleness window at all."""
        burst_in_progress = self._editing_settle_timer.isActive()
        self._editing_settle_timer.start(EDIT_SETTLE_DEBOUNCE_MS)
        stack = self._stack_getter() if self._stack_getter else None
        gpu_eligible = bool(stack is not None and stack.stages
                             and gpu_stack_renderer.is_stack_gpu_eligible(stack))
        if gpu_eligible:
            self._recompute_sample_after(scopes=True)
        else:
            self._recompute_sample_after(downres=burst_in_progress)
        self._render_wipe()
        self._casting_controller.on_stack_or_image_changed()

    def _on_editing_settled(self):
        """Fires once a burst of edits has gone quiet for
        EDIT_SETTLE_DEBOUNCE_MS. If the most recent burst tick was
        downres'd (the CPU-fallback path for a GPU-INeligible stack, see
        refresh_preview), replace it with one final full-quality
        recompute now that things have stopped moving -- if it wasn't
        (an isolated single edit, or a GPU-eligible stack that's always
        at scopes resolution and never needs an "upgrade"), this is a
        no-op rather than a pointless redundant re-render of an already-
        correct (or intentionally-always-small) frame."""
        if not self._last_after_was_downres:
            return
        self._recompute_sample_after(downres=False)
        self._render_wipe()

    def _recompute_sample_after(self, downres=False, scopes=False):
        """Computes (or clears) Sample's post-stack result and notifies
        anything listening (ScopesPanel via after_image_changed) --
        deliberately does NOT touch the wipe display itself (see
        _render_wipe, which reads self._current_after_array back out
        whenever "Sample After" is one of the currently-selected
        sources). Split out from the old _render_after specifically so
        Scopes can keep tracking "the graded result" even when the wipe
        itself is currently showing Reference on both sides.

        `downres`/`scopes` are mutually exclusive (a caller passes at
        most one True) and select which of THREE independent working
        arrays + StageCache pairs to use -- native (_image_array/
        _preview_cache), EDIT_DOWNRES_FACTOR-halved (_image_array_downres/
        _preview_cache_downres, the CPU-fallback burst path for a GPU-
        INeligible stack), or SCOPES_MAX_DIMENSION-capped
        (_image_array_scopes/_preview_cache_scopes, what a GPU-eligible
        stack always uses -- see refresh_preview's own comment). Three
        SEPARATE StageCache instances, not one cache asked about three
        differently-shaped arrays under the same generation-counter key
        -- see _image_array_downres's own __init__ comment for why that
        would hand back wrongly-shaped cached blocks."""
        if scopes:
            working_array = self._image_array_scopes
            cache = self._preview_cache_scopes
        elif downres:
            working_array = self._image_array_downres
            cache = self._preview_cache_downres
        else:
            working_array = self._image_array
            cache = self._preview_cache
        if self._image_array is None or working_array is None:
            self._set_current_after_array(None)
            self._last_after_was_downres = False
            return
        stack = self._stack_getter() if self._stack_getter else None
        if stack is None:
            self._set_current_after_array(None)
            self._last_after_was_downres = False
            return
        # An empty stack is a valid thing to preview (per Tom: "you
        # should be able to preview an empty stack") -- NOT treated the
        # same as "no stack at all" above. apply_stack_to_image_array's
        # own docstring already guarantees an empty stack returns the
        # image unchanged, so this just falls through to the normal
        # apply path below rather than special-casing it.
        try:
            result = apply_stack_to_image_array(
                stack, working_array,
                cache=cache, input_key=self._image_array_generation)
        except Exception as e:  # noqa: BLE001 -- surface any apply failure to the user
            self.status_label.setText(f"Failed to apply stack: {e}")
            self._set_current_after_array(None)
            self._last_after_was_downres = False
            return
        self.status_label.setText("")
        self._last_after_was_downres = downres
        self._set_current_after_array(result)

    def _highlighted_after_array_or_none(self):
        """None unless a matte-producing stage is currently Highlight-
        toggled (self._highlight_getter, see set_highlight_getter) AND
        that stage actually resolved to a matte in the most recent run
        that produced self._current_after_array -- both "no highlight
        toggled" and "the toggled stage was removed/disabled/hasn't run"
        fall back to None here, same silent no-error convention the rest
        of the matte system already uses everywhere (see stack.py's
        docstring, "MATTE CHANNELS"). Picks whichever StageCache tier
        actually produced self._current_after_array -- see
        _recompute_sample_after's own tier-selection comment, mirrored
        here via self._last_after_was_downres rather than duplicating
        that selection logic a third way."""
        if self._highlight_getter is None:
            return None
        highlight_stage_id = self._highlight_getter()
        if highlight_stage_id is None or self._current_after_array is None:
            return None
        cache = self._preview_cache_downres if self._last_after_was_downres else self._preview_cache
        matte = cache.last_mattes().get(highlight_stage_id)
        if matte is None:
            return None
        return _compose_highlight_overlay(self._current_after_array, matte)

    def _array_and_gpu_stack_for_source(self, key, gpu_stack):
        """(array, stage_for_widget_to_grade_or_None, placeholder_message)
        for one of WIPE_SOURCE_CHOICES' three keys -- shared by the
        per-side Left/Right combos (wipe on) and the single Show combo
        (wipe off) in _render_wipe, so all three ever resolve a given
        source the exact same way.

        `gpu_stack` is the live Stack, already confirmed GPU-eligible
        (see gpu_stack_renderer.is_stack_gpu_eligible) and non-empty, or
        None if the stack isn't eligible/is empty -- callers pass the
        SAME value regardless of which key is being resolved; only
        SAMPLE_AFTER actually uses it (SAMPLE_BEFORE/REFERENCE are never
        stack-graded, by definition -- see this module's own docstring).
        When `gpu_stack` is given, SAMPLE_AFTER hands back the RAW
        pre-stack array (self._image_array, always full native
        resolution -- no downres coupling needed here, since GPU grading
        is fast regardless of an edit burst, see refresh_preview's own
        EDIT_DOWNRES_FACTOR comment, which still applies to the CPU-
        fallback case below) for WipeCompareWidget to grade on the GPU
        itself; when `gpu_stack` is None, it hands back the already-CPU-
        computed self._current_after_array instead (see
        _recompute_sample_after, still the source of truth for Scopes
        and for any GPU-ineligible stack).

        MATTE HIGHLIGHT (per Tom: "a way to preview the mask similar to
        resolve's highlight mode"): the CPU branch below is the single
        shared dispatch point Left/Right/Show all funnel through for
        this source, so compositing the highlight HERE (rather than in
        _recompute_sample_after) means it applies correctly regardless
        of wipe layout with no per-side special-casing, and deliberately
        never touches self._current_after_array itself -- Scopes and
        export keep seeing the real graded array; only what gets handed
        to the WIDGET for display is composited. Only ever relevant on
        this CPU branch: a highlight is only meaningful when the
        highlighted stage is an enabled matte-producing stage actually
        in the CURRENT stack, and any such stack is already forced onto
        the CPU path (see stack.MATTE_PRODUCING_KINDS' own docstring on
        why), so `gpu_stack` is already guaranteed None whenever a
        highlight could actually resolve to anything."""
        if key == WIPE_SOURCE_SAMPLE_BEFORE:
            if self._image_array is None:
                return None, None, "Load a source image to preview"
            return self._image_array, None, ""
        if key == WIPE_SOURCE_SAMPLE_AFTER:
            if self._image_array is None:
                return None, None, "Load a source image to preview"
            if gpu_stack is not None:
                return self._image_array, gpu_stack, ""
            if self._current_after_array is None:
                return None, None, "Add stages to the Stack to preview"
            highlighted = self._highlighted_after_array_or_none()
            if highlighted is not None:
                return highlighted, None, ""
            return self._current_after_array, None, ""
        if key == WIPE_SOURCE_REFERENCE:
            if self._reference_image_array is None:
                return None, None, "Load a Reference image to preview"
            return self._reference_image_array, None, ""
        return None, None, ""  # pragma: no cover -- the combos only ever offer the three keys above

    def _render_wipe(self):
        """Central place that pushes whatever's currently selected (Left/
        Right when the wipe's on, just Right when it's off -- see this
        module's docstring on why there's no separate "Show" state) into
        the WipeCompareWidget -- called after literally anything that
        could change what should be on screen: a stack edit
        (refresh_preview), a new Source/Reference image loading, a
        Left/Right menu selection changing, or Enable Wipe being
        toggled. Always recomputes both
        potentially-visible sources (even in single mode, only Right
        matters, but it's the same cheap dispatch either way) rather than
        trying to track which one specifically changed -- see
        _array_and_gpu_stack_for_source, which is O(1) dispatch, not a
        re-decode or a real compute (the actual GPU grading, if any,
        happens lazily inside WipeCompareWidget's own paintGL)."""
        wipe_enabled = self._wipe_enabled
        self.compare.set_wipe_enabled(wipe_enabled)

        stack = self._stack_getter() if self._stack_getter else None
        gpu_stack = (stack if stack is not None and stack.stages
                     and gpu_stack_renderer.is_stack_gpu_eligible(stack) else None)

        if wipe_enabled:
            left_array, left_gpu_stack, _left_placeholder = \
                self._array_and_gpu_stack_for_source(self._wipe_left_key, gpu_stack)
            right_array, right_gpu_stack, right_placeholder = \
                self._array_and_gpu_stack_for_source(self._wipe_right_key, gpu_stack)
            self.compare.set_after_placeholder(right_placeholder)
            self.compare.set_before(left_array, gpu_stack=left_gpu_stack)
            self.compare.set_after(right_array, gpu_stack=right_gpu_stack)
        else:
            array, side_gpu_stack, placeholder = \
                self._array_and_gpu_stack_for_source(self._wipe_right_key, gpu_stack)
            self.compare.set_after_placeholder(placeholder)
            self.compare.set_before(None)
            self.compare.set_after(array, gpu_stack=side_gpu_stack)

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

    def shutdown_casting(self):
        """Called from MainWindow.closeEvent -- stops every active sink
        (today: just the Independent Viewer window, if open; later: a
        DeckLink sink too) before the app quits, rather than leaving a
        stray window (or hardware output) open after LUTier itself has
        already gone away."""
        self._casting_controller.stop_all()

    def _on_cast_toggled(self, checked):
        """Wired to every "Open Independent Viewer Window" QAction built
        by populate_menu -- same cross-instance sync convention as
        _on_wipe_enabled_toggled. self._independent_viewer_sink tracks
        the ONE AirPlayOutputSink instance this menu item owns, so
        toggling off can stop exactly that sink (see CastingController.
        stop_sink) without disturbing any OTHER sink that might be active
        at the same time (a future DeckLink toggle, see casting_
        controller.py's own "fork 3 ways" docstring) -- opening pushes an
        initial frame immediately (see CastingController.start_sink);
        closing tears the native window down."""
        if checked:
            self._independent_viewer_sink = AirPlayOutputSink()
            self._casting_controller.start_sink(self._independent_viewer_sink)
        else:
            self._casting_controller.stop_sink(self._independent_viewer_sink)
            self._independent_viewer_sink = None
        for action in self._casting_actions:
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

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

    def has_sample_image(self):
        """Whether a sample image is currently loaded -- used by
        export_still() itself and by MainWindow's Export dialog "Export
        Still" checkbox (per Tom) to check the same precondition without
        reaching into self._current_path directly from outside this
        class."""
        return self._current_path is not None

    def render_full_resolution_after(self, stack):
        """Reload the source image at full resolution, then apply `stack`
        -- the actual computation shared by export_still() (its own
        file-picker flow, below) and MainWindow's Export dialog "Export
        Still" checkbox (per Tom), so both produce an identically
        processed still without duplicating the reload/apply sequence.
        Reloads the source file at full resolution rather than reusing
        the (deliberately downsampled) preview array -- see this module's
        docstring. Raises whatever load_image_as_array/
        apply_stack_to_image_array itself raises on failure -- callers
        surface that however fits their own UI. Caller is responsible for
        the "a source image is loaded" precondition (see
        has_sample_image) -- this assumes self._current_path is already
        set."""
        full_res = load_image_as_array(self._current_path, max_dimension=None)
        return apply_stack_to_image_array(stack, full_res)

    def render_full_resolution_after_for_capture(self, stack):
        """Same idea as render_full_resolution_after, but works for
        whichever kind of Sample is actually loaded -- a still (delegates
        straight to render_full_resolution_after, the OIIO reload path)
        or a parked VIDEO frame, which has no "reload from path" OIIO
        could take (a video container isn't a still-image format) --
        re-decodes the CURRENT frame at full native resolution via the
        already-open VideoSource instead (seek_to_frame with no
        max_dimension cap, same call scrubbing/stepping already use).
        Used by stack_memories_panel.py's Stills Gallery capture-at-save
        (Phase 3 follow-up, per Tom: "also capture full canvas-res
        still") -- NOT used by Export Still/the Export dialog's "Export
        Still" checkbox, which only ever expect a still-backed Sample and
        keep using render_full_resolution_after directly. Raises
        RuntimeError if the video frame can't be re-decoded (source
        closed/end of stream) -- same "let the caller decide how to
        surface it" convention render_full_resolution_after itself
        follows for its own failures."""
        if self._video_source is not None:
            full_res = self._video_source.seek_to_frame(self._video_frame_index)
            if full_res is None:
                raise RuntimeError("Failed to re-decode the current video frame")
            return apply_stack_to_image_array(stack, full_res)
        return self.render_full_resolution_after(stack)

    def export_still(self):
        """Save the currently previewed "after" result as a real still
        image file, at the ORIGINAL image's full resolution -- not the
        downsampled preview. Same source-of-truth as _render_after (the
        live stack, via get_stack()) so what gets saved always matches
        what's on screen, just at full quality instead of preview
        quality."""
        if not self.has_sample_image():
            QMessageBox.warning(self, "No image", "Load a source image first.")
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
