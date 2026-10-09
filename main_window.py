"""
main_window.py -- top-level app window. Ties BrowserPanel + LUTLibraryPanel
+ StackMemoriesPanel + ScopesPanel + LutViewer3D (its own
panel again, see below) + the Stack together, all backed by app_logic.py,
stack.py, session_io.py, and clf_adapter.py. Export lives in its own
File > Export... dialog (see _show_export_dialog), not part of this spatial
layout at all.

MIGRATED (per Tom, "Yeah. we want free form... do the migration to new
system") from a hand-built QWidget + three hardcoded nested QSplitters to
Qt's native QMainWindow + QDockWidget docking system -- every one of the
nine panels below is now a real QDockWidget that can be dragged to any
edge of the window, dragged into/out of another panel's spot, tabbed with
a neighbor, or torn off into a floating window, all via Qt's own built-in
docking mechanics rather than anything hand-rolled here. See
docs/archive/KNOWN_GOOD_SUMMARY_2026-07-24-6.md for the pre-migration checkpoint and
RUNNING.md's newest entry for the full writeup (including what changed
underneath vs. what's UNVERIFIED ON REAL HARDWARE -- this sandbox has no
real window manager, so the actual drag/tab/tear-off gestures themselves
have never been exercised, only that the right Qt objects/flags/signals
are wired up correctly).

DEFAULT layout (per Tom's original spatial arrangement, now just a
starting POSITION rather than a fixed structure -- see
_build_default_dock_layout, also reachable any time via Workspace >
"Reset to Default Workspace"):

    +----------+----------------------------+--------------+
    | Browser  |                            |  Parameters  |
    |  (top    |          Viewer            +--------------+
    |   left)  |                            |    Scopes    |
    +----------+----------------------------+--------------+
    |  Media   |    Stack -- the stage list |    Graph     |
    |Libraries |                           |  (bottom      |
    +----------+                           |   right)      |
    |   LUT    |                           |              |
    | Library  |                           |              |
    +----------+                           |              |
    | Galleries|                           |              |
    | (bottom  |                           |              |
    |   left)  |                           |              |
    +----------+----------------------------+--------------+
    |          |          Timeline          |              |
    |          |    (bottom center -- the   |              |
    |          |    widest column, per Tom) |              |
    +----------+----------------------------+--------------+

Browser, Media Libraries, LUT Library, and Galleries are four separate
panels (per Tom) -- "Files" is the filesystem LUT browser
(browser_panel.py, unchanged), "Media Libraries" (media_panel.py +
media_library.py, inserted between Browser and LUT Library per Tom) is a
sortable, columned table of still/video files catalogued BY REFERENCE
(see that module's docstring -- an entry never embeds decoded pixel
data, just a path plus header metadata), "LUT Library" (lut_library_panel.py + lut_library.py) is a shelf
of LUTs INGESTED into session memory -- Import... reads a real LUT file
once and keeps the loaded table itself, not just its path, so it (and any
Stack stage built from it, see StackPanel.add_lut_stage_from_library_entry)
can't go offline just because the session moved to a machine that's never
seen the original file, unlike an ordinary "lut" stage's source_path (see
lut_library.py's docstring and session_io.py's docstring on LUT stage
provenance) -- and "Galleries" (stack_memories_panel.py + stack_memory.py
+ cdl_library.py) is a shelf of named, saved Stack snapshots (each also
exportable/importable to its own .ltm file independent of the whole
session, see stack_memory_file_io.py), externally-imported reference
stills, AND named, saved ASC CDL grades all in one union list (per Tom:
"CDL library should also be rolled into the gallery" -- there used to be
a separate "CDL Library" panel/dock here; save a CDL entry from the CDL
stage inspector's "Save to CDL Library..." button, or Import a real
.cc/.cdl/.ccc file, then apply it back onto a CDL stage later, exactly as
before, just from this panel now -- see stack_memories_panel.py's own
docstring for the full row-kind breakdown). All are persisted as part of
the session file (see session_io.py) rather than lost on quit. Recalling
a Stack Memory routes into StackPanel.set_stack, applying a CDL entry
routes into StackPanel.apply_cdl_entry, applying a LUT Library
entry routes into StackPanel.add_lut_stage_from_library_entry, and
applying a Media Libraries entry routes into SampleImagePanel's
Source/Reference slots (see media_panel.py's docstring) -- all
exactly like opening a session does -- none of these panels has a
reference to StackPanel or SampleImagePanel, MainWindow wires all of them
together (self.stack_memories_panel.recall_requested ->
self.stack_panel.set_stack, self.stack_memories_panel.apply_cdl_requested ->
self.stack_panel.apply_cdl_entry, self.stack_panel.cdl_save_requested ->
self.stack_memories_panel.save_cdl_entry, self.lut_library_panel.apply_requested
-> self.stack_panel.add_lut_stage_from_library_entry,
self.media_panel.load_as_sample_requested/
load_as_reference_requested -> self.sample_image.
load_sample_from_library_entry/load_reference_from_library_entry).

Every panel's show/hide control lives in the Workspace menu (see
_build_menu_bar) as a checkable QAction -- now literally each dock
widget's own `toggleViewAction()` rather than a hand-rolled QAction, so
Qt itself keeps the checkbox in sync with the panel's actual visibility
no matter HOW that visibility changed (the menu, a drag, or the panel's
own close button -- see _PanelDockWidget below), instead of this file
manually re-syncing it after every kind of change. Each panel is a
QDockWidget with a custom title bar (see _build_dock_title_bar) showing
its label and, for panels that have one, the same "..." corner menu
button as before (Media Libraries/LUT Library/Galleries/Viewer/Stack/
Scopes/Graph) -- reuses the exact same
#panelTitleRow/#panelTitle object names style.py already styles, so the
visual language is unchanged even though PanelFrame no longer draws its
own title row for these (PanelFrame is still used underneath, purely for
its border/background -- the dock's own title bar replaced PanelFrame's
title-row responsibility; PanelFrame's own title-row capability was
removed entirely 2026-07-25 once it was confirmed dead, see
panel_frame.py's own docstring). A custom title bar
widget does NOT lose Qt's native drag-to-rearrange/drag-to-float
handling -- confirmed against the real installed PySide6 in this sandbox
(see RUNNING.md) that dragging still works from the custom bar the same
as Qt's own default one, though the actual FEEL of that drag on a real
display is one of this migration's UNVERIFIED-ON-REAL-HARDWARE items
(see RUNNING.md's newest entry).

FLOATING (per Tom, Cmd+Shift+1 through Cmd+Shift+9, same digits as each
panel's Shift+N toggle): now just QDockWidget.setFloating(), Qt's own
native mechanism, rather than the hand-rolled setParent()+setWindowFlags()
dance #39 built. Confirmed empirically in this sandbox that a floating
QDockWidget already gets exactly Qt.WindowType.Tool, parented to
MainWindow, with NO Qt.WindowStaysOnTopHint -- i.e. QDockWidget's native
floating already IS the "stays above the main window, not above every
other app" behavior #39 built by hand, now provided for free by the
platform instead of maintained here. A panel can also become floating by
being DRAGGED out (not just via the menu/shortcut) -- every dock widget's
`topLevelChanged` signal is connected to `_on_dock_top_level_changed`,
the single place that keeps `_floating_panels`/the Float-menu checkbox in
sync and triggers `_rebuild_gl_content_for_panel`, regardless of which of
those three ways triggered the change.

_PanelDockWidget (small QDockWidget subclass, right below the imports)
overrides closeEvent so that closing a FLOATING panel via its native
titlebar close button redocks it instead of just hiding it -- reproduces
the exact behavior #29-#32's crash fixes were built and tested against
(Tom's own repro went through this exact path), rather than leaving a
new, never-exercised "hidden while still floating" state Qt would
otherwise leave it in. Closing a DOCKED panel (from the "..." menu's
"Close" if a future panel ever adds one, or DockWidgetClosable's built-in
X) just hides it normally, same as `toggleViewAction` unchecking it.

The hard-won GL-widget crash/render-fix history (RUNNING.md #29-34, since
confirmed fixed on real hardware) is UNCHANGED underneath this migration:
_SafeGLViewWidget's isValid() guard, rebuild_gl_view()'s full-rebuild
(not just a repaint) after any float/dock transition,
_pending_gl_view_deletions keeping a discarded gl_view Python-referenced
across Qt's deferred deleteLater(), and
Qt.ApplicationAttribute.AA_ShareOpenGLContexts (set in main(), before
QApplication is constructed) are all still exactly in place. One
acknowledged gap, called out clearly rather than glossed over: the old
_reparent_gl_safely (setUpdatesEnabled(False) around the reparent call)
still wraps every float/dock transition THIS FILE triggers directly (the
Workspace menu's Float checkbox, the Cmd+Shift+N shortcut), but can't
wrap a transition triggered by the user DRAGGING a panel out interactively
-- Qt performs that reparent internally, mid-drag, with no hook to
suspend paint delivery beforehand. The three defenses above (isValid()
guard, full rebuild after, shared GL contexts) are what's actually relied
on for that path; flag to Tom if dragging the Graph panel specifically
(the one with live GL content) ever produces a visibly broken frame on
real hardware, since that's the one scenario this migration couldn't
directly test against the original crash class.

WORKSPACE PERSISTENCE (get_workspace_state/load_workspace_state) now
delegates to QMainWindow's own saveState()/restoreState() (a versioned
binary blob, base64-encoded for JSON storage) instead of a hand-rolled
dict of splitter sizes/panel visibility/floating set -- Qt's own
mechanism already captures dock position, size, floating state, and
geometry together, correctly, for an arbitrary drag-built arrangement
that no longer has a fixed "which splitter is my home" structure to
serialize by hand. Window size is still tracked separately (saveState()
doesn't cover the top-level window itself). See workspace_io.py's
WORKSPACE_FORMAT_VERSION bump (1 -> 2) -- an old, pre-migration
remembered-workspace/preset file has no `dock_state_b64` key, so it's
treated as empty (falls back to the built-in default layout) rather than
attempted against a dock structure that may not even have the same
object names anymore.

restoreState() is applied via QTimer.singleShot(0, ...), same one-tick
deferral #33 already established for the old floating-restore loop --
building a floating dock widget (with a live QOpenGLWidget child, for the
Graph panel) before the main window has ever been shown or the event
loop has started is the same risky timing #33 diagnosed as the actual
root cause behind #29-#32 never visibly helping on their own, so this
migration keeps the same discipline rather than assuming saveState()/
restoreState() are immune to it.

The Stack (stack_panel.py) replaced four separate panels in an earlier
session -- Controls (Retarget/Resize/Bounds/Printer Lights), Concatenate,
and ACES DRT -- with one reorderable, non-destructive operation stack,
itself split across two spots here: the stage list (center, the main
thing you're building) and Stack Params (top-right, the inspector for
whichever stage is selected) -- see stack_panel.py's docstring for why one
StackPanel object can hand out two separately-placeable widgets like this.
LUT grid size/domain aren't Stack settings: they're export settings
(stack.BakeSettings, self._bake_settings), edited in File > Export. See stack.py's module docstring for the full architecture and
reasoning behind the Stack itself. Loading a file or Identity LUT from the
Browser ADDS a stage to the stack instead of replacing "the current LUT"
-- there's no separate "current LUT" concept anymore, the stack's own
bake IS the current result, always.

There's no Info panel anymore -- it used to show get_lut_info() on
whatever "the current LUT" was, but now that everything is the Stack,
that info was always just describing the same baked stack table (usually
just "LUT3D, size 33" and little else genuinely useful), so it was
removed rather than kept as a panel that always says the same thing.

NOT TESTED IN THIS ENVIRONMENT -- see browser_panel.py / lut_viewer_3d.py
docstrings. Run this file directly first (python3 main_window.py) before
packaging into a .app, same as we did for the metadata patcher GUI. Report
anything that breaks.

Run with: python3 main_window.py
"""
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
    QComboBox, QFileDialog, QMessageBox, QLabel, QCheckBox,
    QDialog, QDialogButtonBox, QPushButton, QProgressDialog, QGroupBox,
)
from PySide6.QtCore import Qt, QTimer, QEvent, QThread, QObject, Signal

from bake_settings_form import BakeSettingsForm, describe_bake_settings
import colour_pipeline
import timeline as tl
import timecode
from style import STYLESHEET, build_dark_palette
from clf_adapter import colour_lut3d_to_clf_node, process_list_wrapping
from format_writers import write_3dl, write_truelight_cub
from app_logic import export_image_array_to_file
import workspace_io
import stack as st
import canvas as cv
import image_io
import video_export
import video_source
import gpu_stack_renderer
from undo_history import UndoHistory
from floating_dialog import make_floating_tool_window, show_floating

import colour
import colour_clf_io as clf

# Names tests reach for on this module (main_window.QFileDialog etc.) --
# kept importable here even where the code using them moved into
# main_window_parts/.
from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: F401,F811
import workspace_io  # noqa: F401,F811
from main_window_parts.constants import PANEL_TOGGLES  # noqa: F401,F811
from main_window_parts.constants import DEFAULT_WORKSPACE_STATE
from main_window_parts.docks import DockMixin
from main_window_parts.layout import LayoutMixin
from main_window_parts.menus import MenuMixin
from main_window_parts.workspace import WorkspaceMixin
from main_window_parts.session import SessionMixin

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

# How long a stream of Stack edits (e.g. dragging a slider) has to go
# quiet before _on_stack_changed_for_history actually commits an undo
# step -- see that method's own docstring. Short enough that a genuine
# pause between two separate, distinct edits (click a different stage,
# adjust a different control) still reliably lands as two separate undo
# steps, long enough to comfortably coalesce every tick of one
# continuous slider drag into a single one.
_UNDO_DEBOUNCE_MS = 400

class _VideoExportWorker(QObject):
    """Runs video_export.export_video_cpu on a background QThread -- the
    FIRST use of a real background thread in this codebase (every prior
    long operation, including LUT baking, has run synchronously on the
    UI thread). Owns its OWN VideoSource (opened fresh on the export's
    own file path, NOT the Viewer's live self.sample_image._video_source
    -- see MainWindow._start_video_export's own comment on why: PyAV
    container/decoder objects aren't documented as thread-safe, so
    sharing the Viewer's own live source between this background thread
    and any live scrub/playback on the UI thread would be a real data
    race, not a hypothetical one).

    `progress`/`finished`/`failed` are the only things this worker
    touches on the Qt side -- everything else (video_export.py,
    stack.apply_stack_to_image_array) is pure Qt-free work, safe to run
    off the UI thread."""

    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, source, stack, encoder, canvas):
        super().__init__()
        self._source = source
        self._stack = stack
        self._encoder = encoder
        self._canvas = canvas
        self._cancel_requested = False

    def request_cancel(self):
        """Safe to call from the UI thread -- just flips a plain bool
        flag video_export.export_video_cpu's own should_cancel callback
        polls between frames, no Qt cross-thread signal machinery needed
        for a single flag like this."""
        self._cancel_requested = True

    def run(self):
        try:
            video_export.export_video_cpu(
                self._source, self._stack, self._encoder, self._canvas,
                progress_callback=lambda done, total: self.progress.emit(done, total),
                should_cancel=lambda: self._cancel_requested,
            )
        except Exception as e:  # noqa: BLE001 -- surface any failure back to the UI thread
            self.failed.emit(str(e))
            return
        self.finished.emit()


class _GpuVideoExportRunner(QObject):
    """Drives GPU-accelerated video export -- the fast path video_export.
    py's own module docstring already anticipated ("the GPU path instead
    drives VideoEncoder.write_frame directly from a UI-thread-chunked
    loop, bypassing export_video_cpu entirely since GPU-graded frames
    never go through stack.apply_stack_to_image_array at all"), deferred
    at the time and built here.

    ALWAYS runs on the UI THREAD -- unlike _VideoExportWorker's CPU path,
    this can NEVER move to a background QThread: it drives the live
    Viewer's own WipeCompareWidget (`panel.compare`) through set_after +
    render_and_read_back_after_frame (see that method's own docstring on
    why a plain repaint()+read_back_after_frame() -- casting_controller.
    py's own pattern for a DIFFERENT purpose -- doesn't work for THIS
    back-to-back-ticks use, and produced a real, reproduced one-frame-
    lag bug here before that method existed). A QOpenGLWidget's GL
    context is only ever safe to touch from the UI thread it lives on --
    reusing that already-proven surface instead of standing up a
    brand-new offscreen
    QOffscreenSurface/QOpenGLContext (never attempted anywhere in this
    app) is a deliberate risk-reduction choice, not an oversight.

    VISIBLE SIDE EFFECT, by design: driving the Viewer this way means the
    user sees each export frame flash through their own Viewer panel
    while a GPU export runs, the same "the Viewer IS the shared
    rendering surface" model realtime playback (video_playback_
    controller.py) already uses, not a separate hidden render target.
    _restore_viewer() re-parks the Viewer back to whatever it was
    actually showing once export finishes/fails/is cancelled, same
    "always clean up, even on the unhappy path" discipline
    VideoPlaybackController.pause() already follows.

    CONCURRENT-EDIT HAZARD: nothing here stops some OTHER code path from
    also calling panel.compare.set_after() while this runner is mid-
    export (a Stack edit would do exactly that via refresh_preview) --
    that would silently corrupt in-flight frames. Deliberately NOT solved
    with a new lock/flag here: MainWindow._start_video_export_gpu reuses
    the exact same WindowModal QProgressDialog the CPU path already sets
    up, which blocks user input to the whole MainWindow for the export's
    duration -- since a Stack edit can only ever originate from user
    input in this app, that already closes the hole with an existing,
    already-tested mechanism rather than a new one.

    CHUNKED via a zero-delay QTimer (one frame per tick) rather than one
    tight synchronous loop over the whole clip -- keeps the UI thread's
    own event loop pumping BETWEEN frames, so the progress dialog
    actually repaints and its Cancel button stays responsive throughout,
    without needing a second thread at all."""

    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, panel, source, stack, encoder, canvas, parent=None):
        super().__init__(parent)
        self._panel = panel
        self._source = source
        self._stack = stack
        self._encoder = encoder
        self._canvas = canvas
        self._frame_index = 0
        self._total = source.frame_count
        self._cancel_requested = False
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()

    def request_cancel(self):
        """Safe to call directly -- everything here already lives on the
        UI thread, no cross-thread flag-passing needed the way
        _VideoExportWorker.request_cancel's own docstring explains for
        its background-QThread case."""
        self._cancel_requested = True

    def _stop(self):
        self._timer.stop()
        self._restore_viewer()

    def _restore_viewer(self):
        """Re-parks the Viewer on whatever it was ACTUALLY showing before
        this export hijacked it -- mirrors VideoPlaybackController.
        pause()'s own "always re-park on the way out" discipline.
        refresh_preview() re-runs the normal Sample-After pipeline from
        the panel's own still-intact underlying state (this runner only
        ever pushed frames into panel.compare directly, it never touched
        SampleImagePanel's own tracked Sample/Reference state at all) --
        exactly the same "something external touched the shared
        rendering surface, please redraw yourself accurately" contract
        StackPanel.stack_changed already relies on this method for."""
        self._panel.refresh_preview()

    def _tick(self):
        if self._cancel_requested or self._frame_index >= self._total:
            self._stop()
            self.finished.emit()
            return
        try:
            array = (self._source.seek_to_frame(0) if self._frame_index == 0
                     else self._source.decode_next_frame())
            if array is None:
                self._stop()
                self.finished.emit()
                return
            if array.shape[1] != self._canvas.width or array.shape[0] != self._canvas.height:
                array = image_io.resize_array(array, self._canvas.width, self._canvas.height)
            self._panel.compare.set_after(array, gpu_stack=self._stack)
            # render_and_read_back_after_frame, NOT repaint()+
            # read_back_after_frame -- see that method's own docstring
            # for the real, reproduced bug this replaced (repaint()
            # doesn't reliably guarantee paintGL has actually finished
            # by the time it returns under this runner's own back-to-
            # back-ticks-with-no-idle-time usage pattern, which made
            # every exported frame show the PREVIOUS frame's grade).
            graded = self._panel.compare.render_and_read_back_after_frame()
            if graded is None:
                raise RuntimeError(
                    "GPU readback failed -- the Viewer panel isn't currently "
                    "paintable (hidden, minimized, or not yet shown once). "
                    "Make sure the Viewer panel is visible and try again.")
            self._encoder.write_frame(graded)
        except Exception as e:  # noqa: BLE001 -- surface any failure, stop cleanly
            self._stop()
            self.failed.emit(str(e))
            return
        self._frame_index += 1
        self.progress.emit(self._frame_index, self._total)


class MainWindow(DockMixin, LayoutMixin, MenuMixin, WorkspaceMixin, SessionMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LUTier")
        self.resize(*DEFAULT_WORKSPACE_STATE["window_size"])

        # LUT grid size/domain for export (stack.BakeSettings) -- a session
        # setting edited in File > Export, not part of the Stack.
        self._bake_settings = st.DEFAULT_BAKE_SETTINGS
        # The Graph panel's live preview bake is skipped while it's hidden
        # and caught up when it's shown -- see _on_stack_changed.
        self._graph_preview_stale = True
        self._session_path = None  # currently open .ltr file, or None (unsaved) -- see _save_session

        # Video export state -- all None except during an active export
        # (see _start_video_export/_video_export_cleanup). _video_export_
        # thread/_worker are only ever set for a GPU-INELIGIBLE stack's
        # CPU/background-QThread path; _video_export_gpu_runner is only
        # ever set for a GPU-eligible stack's UI-thread path -- exactly
        # one of the two is non-None during any single export, never both
        # (see _start_video_export's own dispatch).
        self._video_export_thread = None
        self._video_export_worker = None
        self._video_export_gpu_runner = None
        self._video_export_encoder = None
        self._video_export_source = None
        self._video_export_progress = None

        # Floatable panels (per Tom -- see _float_panel/_dock_panel below)
        # -- frame_attr names (e.g. "browser_frame") whose QDockWidget is
        # currently floating rather than docked in the main window. Kept
        # in sync by _on_dock_top_level_changed regardless of WHAT
        # triggered the change (menu, shortcut, or a user drag -- see this
        # module's docstring). Empty at startup; nothing floats until the
        # user asks via Cmd+Shift+<N>, the Workspace menu, or a drag.
        self._floating_panels = set()

        # Last-known FULLY-DOCKED window layout, as a QMainWindow.
        # saveState() blob (RUNNING.md #59/#60, real bug per Tom -- #58's
        # fix stopped closing a MOVED floating panel from coming back as a
        # totally invalid stray widget, but didn't address the other
        # half: setFloating(False) still has to GUESS where to put the
        # widget back once Qt's own "prior slot" cache is invalidated
        # (which #58's own docstring already noted happens after a real
        # drag). #59's first attempt at fixing that -- tracking a coarse
        # Qt.DockWidgetArea per panel -- turned out to be built on a false
        # premise: EVERY panel in this app's single nested split-tree
        # reports the same dockWidgetArea() (Left, since that's the one
        # area _build_default_dock_layout's very first addDockWidget call
        # used), regardless of which column it's actually nested in, so a
        # "corrective" addDockWidget(that_area, dock) was really just
        # moving panels to an unrelated coarse area -- see
        # _PanelDockWidget.closeEvent's docstring for the full story.
        # saveState()/restoreState() (already relied on elsewhere in this
        # file for whole-session workspace persistence, see
        # get_workspace_state/load_workspace_state) DOES capture and
        # reproduce a panel's exact nested position, confirmed directly --
        # this is that same mechanism, scoped to "restore everything to
        # how it was the last time nothing was floating," refreshed by
        # _snapshot_all_docked_state (called after each place the layout
        # is rebuilt from scratch) and by _on_dock_top_level_changed
        # whenever the LAST floating panel finishes redocking. None while
        # nothing's been snapshotted yet (first-ever run before
        # _build_layout finishes) -- every read site tolerates that.
        self._last_all_docked_state = None

        # Shift-to-dock override (per Tom -- "too eager" redocking with the
        # default Qt behavior). Stock QDockWidget shows its dock-preview
        # indicator and will snap-redock the instant a floating panel is
        # dragged anywhere near an existing dock's edge, which with
        # setDockNestingEnabled(True) is nearly the whole main window --
        # there's no public Qt API to shrink that hit-test margin. Instead
        # of narrowing setAllowedAreas() per panel (which would also block
        # freely relocating a panel to a DIFFERENT column/edge, a
        # confirmed-working feature -- see RUNNING.md #41), every panel's
        # allowedAreas is now flipped to NoDockWidgetArea the instant it
        # STARTS floating (see _on_dock_top_level_changed), so dragging it
        # around never shows a dock-preview or snaps -- Tom's actual ask
        # was the exact inverse of Qt's default: eager-by-default,
        # impossible-to-suppress became suppressed-by-default,
        # eager-on-demand. Holding Shift is what re-enables Qt's normal
        # indicator/snap/redock behavior for as long as it's held (see
        # eventFilter below); releasing it (or just never pressing it)
        # goes back to "drag it anywhere, it'll never jump into a dock."
        # Installed on the QApplication instance, not just self, because a
        # floating panel is its own separate top-level window -- key
        # events during the drag land on THAT widget (or its children),
        # not on MainWindow, so an app-wide filter is the only way to see
        # them regardless of which window currently has focus.
        QApplication.instance().installEventFilter(self)

        # Shutdown-time guard (see _dock_panel/closeEvent) -- True only
        # while _dock_all_floating_panels(rebuild_gl_content=False) is
        # actively docking panels back for a quit already in progress, so
        # _on_dock_top_level_changed's normal rebuild-on-transition
        # behavior can be suppressed for exactly that one case (building a
        # brand new QOpenGLWidget nobody will ever see, at the single
        # riskiest moment for GL work there is, is pure risk with no
        # benefit -- see _dock_panel's docstring).
        self._suppress_gl_rebuild_on_dock_change = False

        # Layout first, menu bar second (reversed from before) -- the new
        # per-category menu-bar menus (see _build_menu_bar) delegate to
        # each panel's own populate_menu(), so those panels need to exist
        # before the menu bar is built. Workspace/File menus don't
        # actually need the panels themselves at build time (Workspace's
        # toggle actions resolve their frame_attr via getattr() lazily,
        # only when clicked), so this reordering doesn't change anything
        # about them.
        self._build_layout()

        # Undo/redo (per Tom, "basic" for now -- see undo_history.py's
        # docstring on scope: tracks the working Stack only). Initialized
        # right after _build_layout (stack_panel exists by now) and
        # before _build_menu_bar (Edit menu needs it to set the initial
        # Undo/Redo enabled state). _restoring_stack_history guards
        # against _on_stack_changed_for_history recording MainWindow's
        # OWN undo()/redo() restore calls as if they were new edits --
        # without it, pressing Undo would immediately push a new history
        # entry for the very state it just restored, making Redo
        # unreachable.
        self._undo_history = UndoHistory(self.stack_panel.get_stack().copy())
        self._restoring_stack_history = False
        # Debounces undo pushes -- see _on_stack_changed_for_history's
        # docstring for why a raw one-push-per-signal approach doesn't
        # work: stack_changed fires on every tick of a live slider drag,
        # not once per gesture.
        self._undo_debounce_timer = QTimer(self)
        self._undo_debounce_timer.setSingleShot(True)
        self._undo_debounce_timer.timeout.connect(self._commit_pending_undo_push)
        self.stack_panel.stack_changed.connect(self._on_stack_changed_for_history)

        self._build_menu_bar()
        self._on_stack_changed()  # push the initial (empty-stack/identity) bake everywhere

        # Remember-between-launches (per Tom) -- applied last, once every
        # action/frame/splitter this could possibly touch already exists.
        # No remembered file yet (first-ever launch on this machine, or it
        # failed to read/parse) just leaves _build_layout's own
        # DEFAULT_WORKSPACE_STATE-derived defaults in place -- see
        # load_workspace_state's own no-op-on-empty-dict handling and
        # workspace_io.load_remembered_workspace's docstring.
        self.load_workspace_state(workspace_io.load_remembered_workspace())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_settling_default_layout", False):
            self._apply_default_dock_proportions()
        if getattr(self, "_pending_dock_state_b64", None) and self._has_reached_maximized_size():
            # Deferred one tick so the dock area has taken the new size.
            QTimer.singleShot(0, self._apply_pending_dock_state)

    def eventFilter(self, obj, event):
        """App-wide Shift-key watcher for the shift-to-dock override (see
        __init__'s docstring on why this has to be app-wide rather than on
        `self`). Every currently-floating panel's allowedAreas flips to
        AllDockWidgetAreas on Shift press (Qt's normal eager dock-preview/
        snap behavior, restored for as long as Shift is held) and back to
        NoDockWidgetArea on release -- deliberately keyed off raw
        KeyPress/KeyRelease rather than a periodic modifiers() poll, so
        this reacts the instant Shift changes state mid-drag rather than
        on some fixed interval. Only touches panels actually in
        `_floating_panels` -- a currently-docked panel's allowedAreas
        stays at Qt's own default (AllDockWidgetAreas, see
        _on_dock_top_level_changed) regardless of Shift, since eagerness
        while rearranging already-docked panels was never part of Tom's
        complaint, only the pop-out-then-reposition case. Autorepeat key
        events (holding Shift down generates repeated KeyPress events, not
        just one) are filtered out via event.isAutoRepeat() -- harmless to
        process redundantly, but there's no reason to."""
        if (event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and event.key() == Qt.Key.Key_Shift
                and not event.isAutoRepeat()
                and self._floating_panels):
            shift_held = event.type() == QEvent.Type.KeyPress
            areas = (Qt.DockWidgetArea.AllDockWidgetAreas if shift_held
                      else Qt.DockWidgetArea.NoDockWidgetArea)
            for frame_attr in self._floating_panels:
                getattr(self, frame_attr).setAllowedAreas(areas)
        # The Graph coming on screen (shown, its tab picked, floated back
        # in) catches up a preview bake skipped while it was hidden --
        # deferred a tick so isVisible() is already true by then.
        if (event.type() == QEvent.Type.Show and obj is self.__dict__.get("viewer_3d")
                and self._graph_preview_stale):
            QTimer.singleShot(0, self._refresh_graph_preview)
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        """Remember-between-launches (per Tom) -- best-effort save of the
        CURRENT workspace state (panel visibility/floating/splitter sizes/
        window size, including whatever's currently floating) to
        workspace_io's fixed remembered-workspace path, read back by
        __init__'s own load_workspace_state(workspace_io.
        load_remembered_workspace()) call on the next launch. Deliberately
        NOT wrapped in a try/except here -- save_remembered_workspace
        itself already swallows any write failure (see that function's
        docstring) so closing the app is never blocked by, e.g., a
        read-only filesystem.

        get_workspace_state() is captured BEFORE _dock_all_floating_
        panels(), not after -- otherwise there'd be nothing left in
        `_floating_panels` to record and a floated panel would silently
        forget it was floating the next launch. Docking everything back
        happens right after, purely so no floating window (a separate
        top-level widget with no Qt-parent relationship to this one, see
        _float_panel) is left standing as an orphan once this window
        actually closes -- an independent open top-level window can
        otherwise keep a Qt app running/visible even after its main
        window is gone.

        rebuild_gl_content=False here -- see _dock_panel's docstring. The
        app is about to quit; there is zero reason to build a brand new
        QOpenGLWidget nobody will ever see, at the single riskiest moment
        for GL work there is (mid-teardown). This was plausibly part of
        why the crash kept recurring even after #29-#32's fixes -- every
        one of Tom's app-quit-with-a-panel-floating tests went through
        this exact path, rebuilding GL content during shutdown."""
        self.sample_image.shutdown_casting()
        state = self.get_workspace_state()
        self._dock_all_floating_panels(rebuild_gl_content=False)
        workspace_io.save_remembered_workspace(state)

        # Shift-to-dock override cleanup (see __init__'s installEventFilter
        # call) -- installed on the QApplication SINGLETON, not on self, so
        # it outlives this MainWindow instance unless explicitly removed
        # here. Matters most for tests: every test that builds a
        # throwaway MainWindow() against the one shared QApplication
        # instance (see test_floating_window_scope.py/
        # test_gl_reparent_safety.py) would otherwise leave a dangling
        # filter registered against a Python object with no other
        # references, one per MainWindow ever constructed for the
        # lifetime of the process -- each additional stale filter still
        # gets consulted on EVERY event the app receives from then on,
        # compounding into a real slowdown across a 25-file test run.
        # Real app usage only ever builds one MainWindow, so this was
        # never a correctness bug there, only a latent one that the test
        # suite's throwaway-instance pattern actually triggers.
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)

    def _capture_gallery_still(self):
        """Wired to StackMemoriesPanel.set_still_capture_getter -- builds
        the full CANVAS-resolution graded array a Stack Memory captures
        alongside its thumbnail at save/update time (Phase 3 follow-up,
        per Tom: "also capture full canvas-res still"). Returns None (no
        capture, never a crash) if there's no Sample loaded, the working
        stack is empty, or the render itself fails -- same tolerance
        stack_memories_panel.py's own capture path already has for a
        missing/failed thumbnail.

        Uses render_full_resolution_after_for_capture (handles BOTH a
        still-backed Sample and a parked VIDEO frame -- see that method's
        own docstring), then resizes to self._canvas.width/height if the
        render doesn't already match -- same per-frame resize convention
        video_export.py's own export_video_cpu already uses, just for one
        frame instead of a whole clip. pixel_aspect_ratio is NOT baked
        into the raster here (a PNG has no standard slot for it the way
        video_export's container-level sample_aspect_ratio does) --
        display-only, same as everywhere else a canvas aspect ratio is
        applied to the Viewer rather than the pixels themselves."""
        if not self.sample_image.has_sample_image():
            return None
        stack = self.stack_panel.get_stack()
        if not stack.stages:
            return None
        try:
            array = self.sample_image.render_full_resolution_after_for_capture(stack)
        except Exception:  # noqa: BLE001 -- best-effort capture, never blocks the actual save
            return None
        c = self._canvas
        if array.shape[1] != c.width or array.shape[0] != c.height:
            array = image_io.resize_array(array, c.width, c.height)
        return array

    def _on_stack_changed(self):
        """Fires on every stage add/remove/reorder/enable-toggle/opacity/
        param edit (StackPanel.stack_changed). No LUT is baked here for
        export -- that happens only in _resolve_export_source, at the
        session's BakeSettings (Tom, 2026-10-05). This only checks the
        stack's structure and refreshes the Graph's preview bake if the
        Graph is on screen (_refresh_graph_preview). Sample Image previews
        the stack directly (see set_stack_getter above).

        A stack can pass through a transiently invalid structural state
        (stack.check_stack_structure) -- most notably, deleting one half of a fork_split/fork_merge pair
        via that row's own "x" button (see stack_panel.py's
        _StageRowWidget.delete_button/_on_row_delete_clicked), which is
        an entirely ordinary, expected single-click action for every
        other stage kind but leaves an unmatched split or merge behind
        here until its other half is removed too. This fires on every
        single edit -- including live slider drags -- so a QMessageBox
        here would be far too disruptive; StackPanel.set_bake_error
        surfaces it inline instead (see its docstring) and the Graph keeps
        its last valid preview rather than crashing the whole app over
        what's very likely a mid-edit, about-to-be-fixed structure."""
        try:
            st.check_stack_structure(self.stack_panel.get_stack().stages)
        except ValueError as e:
            self.stack_panel.set_bake_error(str(e))
            return
        self.stack_panel.set_bake_error(None)
        self._graph_preview_stale = True
        self._refresh_graph_preview()

    def _refresh_graph_preview(self):
        """The Graph panel's live LUT view -- baked at the fixed
        DEFAULT_BAKE_SETTINGS (33³ over [0,1]), never the session's export
        size/domain, and only while the Graph is actually on screen (Tom,
        2026-10-05: "live, only when visible"). A hidden/tabbed-away Graph
        just stays stale; the Show event filter (see eventFilter) catches
        it up the moment it's visible again."""
        if not self._graph_preview_stale or not self.viewer_3d.isVisible():
            return
        try:
            baked = self.stack_panel.get_baked_lut(st.DEFAULT_BAKE_SETTINGS)
        except Exception as e:  # noqa: BLE001 -- a stage's own failure; shown inline, never crashes
            self.stack_panel.set_bake_error(str(e))
            return
        self._graph_preview_stale = False
        self.viewer_3d.set_lut(baked)

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

    # ---- Sequence/Shot toggle (see stack_panel.py's own docstring) -------

    def _display_transform(self):
        c = self._canvas
        return colour_pipeline.DisplayTransform(c.display_drt, c.display_name, c.display_view)

    def _effective_stack(self, user_stack, input_colourspace):
        """The Stack Record actually renders (colour_pipeline.py): the
        source's input transform, each stage in its own space, and the
        project's display transform, around `user_stack`."""
        return colour_pipeline.build_effective_stack(
            user_stack, self._canvas.working_colourspace, input_colourspace, self._display_transform())

    def _input_colourspace_for_path(self, path):
        """The input color space of the Media Library entry for `path`, or
        None (not in a library / already in the working space)."""
        if not path:
            return None
        for library in self.media_panel.get_pool().libraries:
            for entry in library.entries:
                if entry.source_path == path:
                    return entry.source_colourspace
        return None

    def _timeline_pipeline(self, clip, composed):
        return self._effective_stack(composed, clip.input_colourspace)

    def _on_input_colourspace_changed(self, entry):
        """A Media Library entry's input space changed: every timeline clip
        of that file follows it, and Record re-renders."""
        for library in self.media_panel.get_pool().libraries:
            for timeline_obj in library.timelines:
                for clip in timeline_obj.clips:
                    if clip.source_path == entry.source_path:
                        clip.input_colourspace = entry.source_colourspace
        self.sample_image.refresh_preview()

    def _composed_preview_stack(self):
        """The Viewer's own live-preview stack -- ALWAYS the currently
        selected Timeline clip's own grade composed with the Sequence
        grade together (stack.compose_stacks), regardless of which ONE
        of them the Sequence/Shot toggle currently has loaded for
        editing (Tom, after trying the build: "the result i see in the
        viewer should always be the result of the clip level grade and
        then the timeline level grade... right now when i switch back
        and forth it only shows one or the other"). Deliberately NOT
        stack_panel.get_stack() (which answers a different question --
        "what is StackPanel's editor currently showing," Sequence OR
        Shot, never both -- see that method's own docstring) and NOT
        get_sequence_stack() alone either.

        Falls back to the Sequence stack alone when no Timeline clip is
        selected -- ordinary non-Timeline Source/Reference grading,
        unchanged from before this feature existed. Wired as sample_
        image's own stack_getter (see _build_layout), so this same
        composition automatically covers the Viewer AND Scopes (both
        read through that one getter already -- see sample_image_panel.
        py's refresh_preview) without needing a second wire for each."""
        clip = self.timeline_panel.get_selected_clip()
        sequence_stack = self.stack_panel.get_sequence_stack()
        if clip is None:
            input_space = self._input_colourspace_for_path(self.sample_image.current_path)
            user_stack = sequence_stack
        else:
            input_space = clip.input_colourspace
            user_stack = st.compose_stacks(clip.stack, sequence_stack)
        self._refresh_pipeline_summary(input_space)
        return self._effective_stack(user_stack, input_space)

    def _refresh_pipeline_summary(self, input_space):
        c = self._canvas
        if c.display_drt == "aces":
            display = f"{c.display_view} ({c.display_name.replace(' - Display', '')})"
        else:
            display = "none (working space)"
        self.stack_panel.set_pipeline_summary(input_space, c.working_colourspace, display)

    def _on_sequence_mode_requested(self):
        """The user clicked "Sequence" -- wrapped in _restoring_stack_
        history, same reasoning as _undo/_redo/_new_session/_open_session's
        own set_stack() calls: switching which Stack is loaded into the
        editor is a wholesale replace, not a genuine edit, and must never
        itself create a spurious undo entry (stack_panel.py's
        activate_sequence_mode/activate_shot_mode both fire stack_changed,
        same as any other stack swap). StackPanel can't set this flag
        itself -- it has no reference to MainWindow -- hence routing the
        actual activation back out through here instead of letting the
        button call activate_sequence_mode() directly."""
        self._restoring_stack_history = True
        try:
            self.stack_panel.activate_sequence_mode()
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()

    def _on_shot_mode_requested(self, clip):
        """The user clicked "Shot" -- same wrapping reasoning as
        _on_sequence_mode_requested above."""
        self._restoring_stack_history = True
        try:
            self.stack_panel.activate_shot_mode(clip)
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()

    def _on_timeline_clip_selected(self, clip):
        """TimelinePanel.clip_selected fired -- same wrapping reasoning as
        _on_sequence_mode_requested above: notify_selected_clip_changed
        can itself call activate_sequence_mode/activate_shot_mode (re-
        pointing Shot mode at a newly selected clip, or falling back to
        Sequence if the selection was cleared), which must not create a
        spurious undo entry either.

        Also loads the newly selected clip's own footage into the
        Viewer's Source slot (Tom, after trying the skeleton build:
        "theres no way to use the timeline panel to select which clip
        you want to work on" / "it defaults to the sample test image" --
        selecting a clip previously only swapped which Stack Shot mode
        would edit, with nothing to show it against; a Shot-mode grade
        with no visible connection to the footage it's grading is not
        usable). Reuses load_video/load_image exactly as if the user had
        picked "Load as Source" on that same file -- this is what makes
        the EXISTING scrub bar (see sample_image_panel.py's own
        "transport exclusive to timeline, scrub exclusive to clip" split)
        actually have something to scrub once a clip is selected. Does
        nothing on deselection (clip is None) -- leaves whatever was
        already loaded as Source alone rather than clearing it.

        Also re-points TimelineController at this clip (seek_to_clip) so
        the NEXT Play starts here, not wherever an earlier play/pause
        session left off -- see that method's own docstring."""
        self._restoring_stack_history = True
        try:
            self.stack_panel.notify_selected_clip_changed(clip)
        finally:
            self._restoring_stack_history = False
        self._update_undo_redo_actions()
        if clip is not None:
            self.sample_image.activate_record_mode()
            if clip.media_kind == "video":
                self.sample_image.load_video(clip.source_path)
            else:
                self.sample_image.load_image(clip.source_path)
            # Also points a SUBSEQUENT Play at this clip -- see
            # TimelineController.seek_to_clip's own docstring.
            clip_index = self.timeline_panel.get_selected_clip_index()
            if clip_index is not None:
                self.sample_image._timeline_controller.seek_to_clip(clip_index)

    def _on_timeline_seek_requested(self, record_frame):
        """TimelinePanel.seek_requested fired -- a click OR a continuous
        drag on the Timeline view's own ruler/empty track (see
        TimelineTrackWidget's own mousePressEvent/mouseMoveEvent, which
        fire this repeatedly while dragging, not just once). Switches
        the Viewer to RECORD first (see SampleImagePanel.
        activate_record_mode's own docstring on why: without it, the
        Timeline view's playhead would move while the transport's scrub
        bar/labels stayed frozen on a stale SOURCE position, a visible
        desync), then routes the seek through the transport's own scrub
        bar (setValue -> _on_video_scrub_slider_changed) rather than
        calling TimelineController.seek_to_record_frame directly -- this
        is what gives a Timeline-view drag-scrub the same scrub-time
        downres treatment (SCRUB_DOWNRES_FACTOR) a drag on the
        horizontal scrub bar already gets, and keeps that scrub bar's
        own displayed value in step with wherever the Timeline view's
        playhead is being dragged to."""
        self.sample_image.activate_record_mode()
        self.sample_image._video_scrub_slider.setValue(record_frame)

    def _on_media_entry_dropped_on_timeline(self, entry_id, index):
        """TimelinePanel.media_entry_drop_requested fired -- a Media
        Library row dragged onto the Timeline view (per Tom: "make it
        more similar to a typical NLE"). Resolves `entry_id` against
        MediaPanel's own library (the Timeline panel has no reference to
        it) and inserts a full-range clip at `index`. Silently a no-op
        if the entry was removed from the library between the drag
        starting and landing -- same "stale id, just drop it" tolerance
        TimelinePanel._selected_clip's own KeyError guards use elsewhere
        for a vanished row."""
        try:
            entry = self.media_panel.find_entry(entry_id)
        except KeyError:
            return
        self.timeline_panel.insert_clip_from_library_entry_at(entry, index)

    def _open_in_source_viewer(self, entry):
        self.source_viewer_frame.setVisible(True)
        self.source_viewer_frame.raise_()
        self.source_viewer.load_entry(entry)

    def _on_source_edit_requested(self, mode, spec):
        """Source viewer's Insert / Overwrite / Append: the marked range of
        the Source clip into the loaded timeline (a new one is made in the
        current library if none is loaded), at Record's playhead."""
        timeline_obj = self.timeline_panel.get_timeline()
        if timeline_obj is None:
            self.media_panel.new_timeline()
            timeline_obj = self.timeline_panel.get_timeline()
            if timeline_obj is None:
                return
        in_frame, out_frame = spec["in_frame"], spec["out_frame"]
        if spec["kind"] != "video":
            in_frame, out_frame = 0, self.timeline_panel.still_hold_out_frame()
        clip = tl.TimelineClip(spec["name"], spec["path"], spec["kind"], in_frame, out_frame,
                               thumbnail=spec.get("thumbnail"))
        record_frame = self.sample_image._timeline_controller.current_record_frame() or 0
        self.timeline_panel.apply_edit(mode, clip, record_frame)
        self.sample_image.activate_record_mode()

    def match_frame(self):
        """Match Frame (F): load the clip under Record's playhead into the
        Source viewer, on the same source frame."""
        controller = self.sample_image._timeline_controller
        timeline_obj = self.timeline_panel.get_timeline()
        record_frame = controller.current_record_frame()
        if timeline_obj is None or not timeline_obj.clips or record_frame is None:
            return
        clip_index, offset = controller._locate_record_frame(timeline_obj, record_frame)
        clip = timeline_obj.clips[clip_index]
        start_frame = 0
        for library in self.media_panel.get_pool().libraries:
            entry = next((e for e in library.entries if e.source_path == clip.source_path), None)
            if entry is not None:
                tc = entry.metadata.get("start_timecode")
                start_frame = timecode.timecode_to_frames(tc, entry.frame_rate or 0) or 0 if tc else 0
                break
        self.source_viewer_frame.setVisible(True)
        self.source_viewer.load_path(clip.source_path, clip.media_kind, clip.name,
                                     frame_index=clip.in_frame + offset, start_frame=start_frame,
                                     thumbnail=clip.thumbnail)

    def _load_timeline(self, timeline):
        """Open `timeline` (a Media Library timeline, or None to unload) in
        the Timeline panel. Stops Timeline playback first and drops any
        Shot-mode clip selection, same as New/Open Session do when they
        replace the timeline."""
        if timeline is self.timeline_panel.get_timeline():
            return
        self.sample_image._timeline_controller.stop()
        self.stack_panel.notify_selected_clip_changed(None)
        self.timeline_panel.load_timeline(timeline)
        if timeline is not None:
            self.sample_image.activate_record_mode()

    def _on_library_timeline_renamed(self, timeline):
        if timeline is self.timeline_panel.get_timeline():
            self.timeline_panel.refresh_timeline_name()

    def _on_library_timeline_deleted(self, timeline):
        if timeline is self.timeline_panel.get_timeline():
            self._load_timeline(None)

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

    def _show_export_video_dialog(self):
        """File > Export Video... (per Tom, 2026-08-13) -- same lazy-
        built-cached-QDialog shape as _show_export_dialog/
        _show_canvas_settings_dialog. Requires a video currently loaded
        in the Viewer (self.sample_image._video_source) -- guarded here
        rather than disabling the menu action itself, matching this
        menu's existing "always visible, guard at the point of use"
        convention (see _show_canvas_settings_dialog's own lack of a
        disabled-state check for comparison).

        CPU-path export only for this pass (Tom's approved plan's own
        GPU-eligible-stack fast path is a deliberate, documented follow-
        up, not built here) -- video_export.export_video_cpu (via
        stack.apply_stack_to_image_array) grades ANY stack correctly
        regardless of GPU eligibility; that check only ever mattered for
        LIVE PREVIEW performance, never export correctness, so every
        stack exports through the exact same, already-verified path."""
        if self.sample_image._video_source is None:
            QMessageBox.information(
                self, "No video loaded",
                "Load a video in the Viewer first (File > Load Video... "
                "or the Viewer's own ☰ menu).")
            return

        if getattr(self, "_export_video_dialog", None) is None:
            dialog = QDialog(self)
            make_floating_tool_window(dialog)
            dialog.setWindowTitle("Export Video")

            outer = QVBoxLayout(dialog)

            path_row = QHBoxLayout()
            path_row.addWidget(QLabel("Output File"))
            self.export_video_path_label = QLabel("(not set)")
            self.export_video_path_label.setWordWrap(True)
            path_row.addWidget(self.export_video_path_label, stretch=1)
            browse_button = QPushButton("Browse...")
            browse_button.clicked.connect(self._on_export_video_browse_clicked)
            path_row.addWidget(browse_button)
            outer.addLayout(path_row)

            codec_row = QHBoxLayout()
            codec_row.addWidget(QLabel("Codec"))
            self.export_video_codec_combo = QComboBox()
            for preset in video_export.available_codec_presets():
                self.export_video_codec_combo.addItem(preset["label"], preset["label"])
            self.export_video_codec_combo.currentIndexChanged.connect(
                self._on_export_video_codec_changed)
            codec_row.addWidget(self.export_video_codec_combo, stretch=1)
            outer.addLayout(codec_row)

            depth_row = QHBoxLayout()
            depth_row.addWidget(QLabel("Bit Depth"))
            self.export_video_bit_depth_combo = QComboBox()
            depth_row.addWidget(self.export_video_bit_depth_combo, stretch=1)
            outer.addLayout(depth_row)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            export_button = button_box.addButton(
                "Export...", QDialogButtonBox.ButtonRole.AcceptRole)
            export_button.clicked.connect(self._start_video_export)
            button_box.rejected.connect(dialog.close)
            outer.addWidget(button_box)

            self._export_video_dialog = dialog
            self._export_video_output_path = None
            self._on_export_video_codec_changed()  # populate the bit-depth combo initially

        show_floating(self._export_video_dialog)

    def _selected_export_video_codec_preset(self):
        label = self.export_video_codec_combo.currentData()
        return video_export.get_codec_preset(label) if label is not None else None

    def _on_export_video_browse_clicked(self):
        preset = self._selected_export_video_codec_preset()
        default_ext = preset["extension"] if preset else ".mov"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Video", f"export{default_ext}",
            f"Video (*{default_ext})",
        )
        if path:
            self._export_video_output_path = path
            self.export_video_path_label.setText(path)

    def _on_export_video_codec_changed(self, _index=None):
        """Repopulates the Bit Depth combo to whatever THIS codec preset
        can actually carry (see video_export.CODEC_PRESETS' own
        max_bit_depth) -- "32f" is never offered at all, no codec this
        app offers can carry float samples (see video_export.py's own
        docstring)."""
        preset = self._selected_export_video_codec_preset()
        self.export_video_bit_depth_combo.clear()
        if preset is None:
            return
        for depth in cv.BIT_DEPTHS:
            if depth == "32f" or depth > preset["max_bit_depth"]:
                continue
            self.export_video_bit_depth_combo.addItem(cv.BIT_DEPTH_LABELS[depth], depth)
        # Default to the canvas's own bit depth if this codec can carry
        # it, otherwise the highest depth it CAN carry.
        idx = self.export_video_bit_depth_combo.findData(self._canvas.bit_depth)
        if idx < 0:
            idx = self.export_video_bit_depth_combo.count() - 1
        if idx >= 0:
            self.export_video_bit_depth_combo.setCurrentIndex(idx)

    def _start_video_export(self):
        if self._export_video_output_path is None:
            QMessageBox.warning(self, "No output file", "Choose an output file first.")
            return
        preset = self._selected_export_video_codec_preset()
        bit_depth = self.export_video_bit_depth_combo.currentData()
        if preset is None or bit_depth is None:
            return

        live_source = self.sample_image._video_source
        if live_source is None:
            QMessageBox.warning(self, "No video loaded", "Load a video in the Viewer first.")
            return

        # A FRESH VideoSource on the same file, independent of the
        # Viewer's own live self.sample_image._video_source -- see
        # _VideoExportWorker's own docstring on why: this decodes on a
        # background thread for the whole export's duration, and PyAV
        # container/decoder objects aren't documented thread-safe, so
        # sharing the Viewer's live source with any scrub/playback the
        # user does DURING export would be a real data race.
        try:
            export_source = video_source.VideoSource(live_source.path)
        except Exception as e:  # noqa: BLE001 -- surface any open failure to the user
            QMessageBox.critical(self, "Failed to start export", str(e))
            return

        canvas = self._canvas
        try:
            encoder = video_export.VideoEncoder(
                self._export_video_output_path, canvas.width, canvas.height,
                export_source.frame_rate, preset, bit_depth=bit_depth,
                pixel_aspect_ratio=canvas.pixel_aspect_ratio)
        except Exception as e:  # noqa: BLE001 -- surface any encoder-open failure to the user
            export_source.close()
            QMessageBox.critical(self, "Failed to start export", str(e))
            return

        stack = self.stack_panel.get_stack()
        self._export_video_dialog.close()

        # Held on self, not local variables closed over by a lambda --
        # see _video_export_cleanup's own docstring on why every one of
        # this export's signal connections below goes to a real BOUND
        # METHOD of self (a QObject whose thread affinity is the UI
        # thread) rather than a plain lambda/closure.
        self._video_export_encoder = encoder
        self._video_export_source = export_source
        self._video_export_output_path_pending = self._export_video_output_path

        self._video_export_progress = QProgressDialog(
            "Exporting video...", "Cancel", 0, max(1, export_source.frame_count), self)
        self._video_export_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._video_export_progress.setMinimumDuration(0)
        self._video_export_progress.setValue(0)

        # GPU-eligible stack -- see _GpuVideoExportRunner's own docstring
        # for why this branches to an entirely different (UI-thread,
        # no QThread) execution shape rather than reusing
        # _VideoExportWorker with a GPU flag.
        if gpu_stack_renderer.is_stack_gpu_eligible(stack):
            self._start_video_export_gpu(export_source, stack, encoder, canvas)
        else:
            self._start_video_export_cpu(export_source, stack, encoder, canvas)

    def _start_video_export_cpu(self, export_source, stack, encoder, canvas):
        self._video_export_thread = QThread(self)
        self._video_export_worker = _VideoExportWorker(export_source, stack, encoder, canvas)
        self._video_export_worker.moveToThread(self._video_export_thread)
        self._video_export_thread.started.connect(self._video_export_worker.run)
        # Bound methods of THIS QObject (self), not lambdas -- Qt's
        # auto-connection can only recognize a signal/slot pair as
        # cross-thread (and therefore correctly QUEUE delivery onto the
        # receiver's own thread) when it can determine the RECEIVER's
        # thread affinity from the connection itself. A plain Python
        # lambda has no QObject/thread affinity of its own, so PySide6
        # can't reliably queue it -- confirmed directly (a real bug,
        # caught by an end-to-end smoke test the CPU-path unit tests
        # never would have caught, since those never actually ran the
        # worker on a real second QThread): connecting worker.finished/
        # worker.progress to plain lambdas produced "QObject::killTimer:
        # Timers cannot be stopped from another thread" and
        # "QWidget::repaint: Recursive repaint detected" warnings, i.e.
        # the "cleanup" work was runing on the WORKER thread instead of
        # being safely queued back onto the UI thread.
        self._video_export_worker.progress.connect(self._on_video_export_progress)
        self._video_export_worker.finished.connect(self._on_video_export_finished)
        self._video_export_worker.failed.connect(self._on_video_export_failed)
        self._video_export_progress.canceled.connect(self._video_export_worker.request_cancel)

        self._video_export_thread.start()

    def _start_video_export_gpu(self, export_source, stack, encoder, canvas):
        """GPU-eligible stack -- see _GpuVideoExportRunner's own
        docstring for the full reasoning (UI-thread only, drives the
        live Viewer directly). The WindowModal progress dialog set up by
        the caller does double duty here: besides showing progress, it
        blocks user input to this MainWindow for the export's whole
        duration, which is what stops a concurrent Stack edit from
        hijacking the Viewer's shared rendering surface mid-export (see
        the runner's own docstring on that hazard)."""
        self._video_export_gpu_runner = _GpuVideoExportRunner(
            self.sample_image, export_source, stack, encoder, canvas, parent=self)
        self._video_export_gpu_runner.progress.connect(self._on_video_export_progress)
        self._video_export_gpu_runner.finished.connect(self._on_video_export_finished)
        self._video_export_gpu_runner.failed.connect(self._on_video_export_failed)
        self._video_export_progress.canceled.connect(self._video_export_gpu_runner.request_cancel)

        self._video_export_gpu_runner.start()

    def _on_video_export_progress(self, done, _total):
        self._video_export_progress.setValue(done)

    def _on_video_export_finished(self):
        self._video_export_cleanup(success=True)

    def _on_video_export_failed(self, error_message):
        self._video_export_cleanup(success=False, error_message=error_message)

    def _video_export_cleanup(self, success, error_message=None):
        """The ONE place every video export -- successful, failed, or
        cancelled -- ends up, always called via a real bound-method
        signal connection (see _start_video_export's own comment on why
        that matters for correct cross-thread delivery) so this always
        actually runs on the UI thread, safe to touch self._video_export_
        progress/QMessageBox/etc. directly. Handles BOTH the CPU
        (background QThread) and GPU (UI-thread runner) shapes -- exactly
        one of self._video_export_thread/self._video_export_gpu_runner is
        set for any given export (see __init__'s own comment), so only
        that one's teardown actually runs; the GPU runner has already
        stopped its own QTimer and re-parked the Viewer (see
        _GpuVideoExportRunner._stop) by the time finished/failed fires,
        so there's nothing further to do for it here beyond dropping the
        reference below."""
        self._video_export_encoder.close()
        self._video_export_source.close()
        if self._video_export_thread is not None:
            self._video_export_thread.quit()
            self._video_export_thread.wait()
        self._video_export_progress.close()
        if error_message:
            QMessageBox.critical(self, "Video export failed", error_message)
        elif success:
            QMessageBox.information(
                self, "Export complete", f"Exported to:\n{self._video_export_output_path_pending}")
        # Drop the strong references -- PySide6 doesn't keep a
        # QThread/QObject alive on its own once _start_video_export
        # returns, only Qt parent-child ownership (the thread has `self`
        # as parent, the worker doesn't) or an explicit reference like
        # this does, so these had to be held on self for the export's
        # whole duration.
        self._video_export_thread = None
        self._video_export_worker = None
        self._video_export_gpu_runner = None
        self._video_export_encoder = None
        self._video_export_source = None
        self._video_export_progress = None

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


def main():
    """Entry point for running this module directly (`python3
    main_window.py`) -- constructs its own QApplication and shows the
    real window immediately, with none of splash_launcher.py's splash
    (that's the actual packaged-app entry point, see setup.py's APP
    list). Skipping the splash is the point here: useful during
    development, when this module's own ~5-second import cost (colour-
    science/pyqtgraph/OpenColorIO/scipy, all loaded transitively by the
    imports at the top of this file) isn't worth padding with a splash
    that would just sit there through the exact same wait. See
    build_window() below for the part splash_launcher.py actually
    shares with this function."""
    # THE ACTUAL ROOT CAUSE of the 3D Viewer's blank/black rendering, per
    # the real traceback Tom pasted (glUseProgram raising GLError 1281/
    # 1282, "invalid value"/"invalid operation", from inside pyqtgraph's
    # own GLLinePlotItem/GLScatterPlotItem.paint()). Both of those classes
    # cache their compiled shader PROGRAM at the CLASS level (`klass.
    # _shaderProgram`, see GLScatterPlotItem.getShaderProgram/
    # GLLinePlotItem.getShaderProgram in pyqtgraph's own source) --
    # compiled exactly once, ever, in whichever GL context happens to be
    # current the FIRST time any instance of that class is ever painted,
    # then reused forever after by every future instance regardless of
    # which widget/context is asking. A compiled program's integer ID is
    # only meaningful WITHIN the context it was created in -- by default,
    # separate top-level QOpenGLWidgets (which is what floating a panel,
    # or rebuild_gl_view's fresh widget, both create) each get their OWN
    # independent GL context. So the very first widget to ever paint a
    # scatter/line item successfully "poisons" that class-level cache for
    # every OTHER widget for the rest of the process -- exactly matching
    # what Tom saw: blank the whole time (every paint attempt fails with
    # this error), AND plausibly the renewed segfault on quit too (
    # repeatedly calling GL functions with a stale/invalid program handle
    # is undefined behavior, not just a clean error return every time).
    #
    # QOpenGLWidget's own docs name this exact scenario and the fix:
    # Qt.ApplicationAttribute.AA_ShareOpenGLContexts makes every
    # QOpenGLWidget's context part of one shared "share group" instead of
    # being fully independent, so a resource (like a compiled shader
    # program) created via any one of them stays valid in all the others
    # too. MUST be set before QApplication is constructed -- Qt reads it
    # exactly once, at that point.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # NOTE for docs/archive/GPU_ACCELERATION_PLAN_2026-08-08.md's Stage 5: no
    # QSurfaceFormat override is set here on purpose. wipe_compare_
    # widget.py's own GPU pipeline targets GLSL 1.20/OpenGL 2.1
    # Compatibility Profile specifically because that's what this Mac's
    # true platform-default QOpenGLWidget context already is (confirmed
    # directly via GL_VERSION/GL_SHADING_LANGUAGE_VERSION) -- forcing a
    # newer Core Profile app-wide was tried and confirmed on real
    # hardware to break the Graph panel's pyqtgraph rendering outright
    # (its GLScatterPlotItem/GLLinePlotItem code never binds its own
    # Vertex Array Object, which Core Profile requires but Compatibility
    # Profile doesn't -- a real ShaderValidationError, not a guess). The
    # platform default is already exactly right; don't "helpfully" add a
    # format override back here.
    app = QApplication(sys.argv)
    window = build_window(app)
    sys.exit(app.exec())


def build_window(app):
    """Applies this app's fixed styling to `app` and constructs+shows a
    MainWindow, returning it -- split out of main() so splash_launcher.py
    (the app's actual entry point, see setup.py's APP list -- shows a
    QSplashScreen using ITS OWN already-constructed QApplication before
    `import main_window` even happens, since that import alone is the
    slow part: colour-science/pyqtgraph/OpenColorIO/scipy all load
    transitively through this module's own top-level imports) can reuse
    the SAME QApplication instead of main() constructing a second one --
    Qt only ever supports one QApplication per process. main() (used when
    running `python3 main_window.py` directly, skipping the splash --
    handy during development, when waiting on it isn't worth it) still
    constructs its own app and calls this with it, so this function's
    behavior is identical either way; only who calls app.exec() and when
    the splash gets finish()ed differs between the two entry points.

    Does NOT call app.exec() -- that's each caller's own job, since
    splash_launcher.py needs to finish() the splash in between
    window.show() and the actual exec() call."""
    # macOS's native ("Aqua") widget style largely ignores QSS on
    # QPushButton -- background-color, border, and checked-state styling
    # in particular get silently dropped, which is almost certainly why
    # the toggle buttons rendered as plain text with no visible on/off
    # state despite style.py's :checked rule being correct. Fusion is
    # Qt's own cross-platform style and actually honors stylesheets; this
    # trades a little native-macOS look-and-feel for the stylesheet
    # working at all. Must be set before any widgets are constructed.
    app.setStyle("Fusion")
    # Fixed dark palette (style.py NINTH PASS, RUNNING.md #62) -- per Tom,
    # the app should look the same dark-gray-with-blue-accents theme
    # regardless of the OS's own light/dark setting, not just tolerate a
    # dark OS theme (real screenshots in light mode looked bad -- washed
    # out, poor contrast). MUST be set before setStyleSheet(), since every
    # `palette(...)` reference in STYLESHEET resolves against whatever
    # QPalette the QApplication holds AT THE TIME those rules are
    # evaluated -- setting our fixed palette first means every one of
    # those references (and any native/unstyled widget this app doesn't
    # explicitly QSS-style) draws from OUR colors, not the OS's.
    app.setPalette(build_dark_palette())
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show_on_launch()
    return window


if __name__ == "__main__":
    main()
