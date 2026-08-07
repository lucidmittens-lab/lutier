"""
main_window.py -- top-level app window. Ties BrowserPanel + LUTLibraryPanel
+ CDLLibraryPanel + StackMemoriesPanel + ScopesPanel + LutViewer3D (its own
panel again, see below) + the Stack together, all backed by app_logic.py,
stack.py, session_io.py, and clf_adapter.py. Export lives in its own
File > Export... dialog (see _show_export_dialog), not part of this spatial
layout at all.

MIGRATED (per Tom, "Yeah. we want free form... do the migration to new
system") from a hand-built QWidget + three hardcoded nested QSplitters to
Qt's native QMainWindow + QDockWidget docking system -- every one of the
ten panels below is now a real QDockWidget that can be dragged to any
edge of the window, dragged into/out of another panel's spot, tabbed with
a neighbor, or torn off into a floating window, all via Qt's own built-in
docking mechanics rather than anything hand-rolled here. See
KNOWN_GOOD_SUMMARY_2026-07-24-6.md for the pre-migration checkpoint and
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
    |  Image   |    Stack -- the stage list |    Graph     |
    | Library  |    (center, main area)    |  (bottom      |
    +----------+                           |   right)      |
    |   LUT    |                           |              |
    | Library  |                           |              |
    +----------+                           |              |
    |   CDL    |                           |              |
    | Library  |                           |              |
    +----------+                           |              |
    |  Stack   |                           |              |
    | Memories |                           |              |
    | (bottom  |                           |              |
    |   left)  |                           |              |
    +----------+---------------------------+--------------+

Browser, Image Library, LUT Library, CDL Library, and Stack Memories are
five separate panels (per Tom) -- "Files" is the filesystem LUT browser
(browser_panel.py, unchanged), "Image Library" (image_library_panel.py +
image_library.py, inserted between Browser and LUT Library per Tom) is a
shelf of reference images ingested into session memory (see that module's
docstring for the one "embedded in memory, path-referenced on disk"
caveat), "LUT Library" (lut_library_panel.py + lut_library.py) is a shelf
of LUTs INGESTED into session memory -- Import... reads a real LUT file
once and keeps the loaded table itself, not just its path, so it (and any
Stack stage built from it, see StackPanel.add_lut_stage_from_library_entry)
can't go offline just because the session moved to a machine that's never
seen the original file, unlike an ordinary "lut" stage's source_path (see
lut_library.py's docstring and session_io.py's docstring on LUT stage
provenance) -- "CDL Library" (cdl_library_panel.py + cdl_library.py) is a
shelf of named, saved ASC CDL grades -- save one from the CDL stage
inspector's "Save to CDL Library..." button, or Import a real
.cc/.cdl/.ccc file (parsed values copied straight into the entry, same
"copied in, not referenced" philosophy as the LUT Library, just for a
handful of floats instead of a whole table), then apply it back onto a
CDL stage later -- and "Stack Memories" (stack_memories_panel.py +
stack_memory.py) is a shelf of named, saved Stack snapshots, each also
exportable/importable to its own .ltm file independent of the whole
session (see stack_memory_file_io.py). All are persisted as part of the
session file (see session_io.py) rather than lost on quit. Recalling a
Stack Memory routes into StackPanel.set_stack, applying a CDL Library
entry routes into StackPanel.apply_cdl_entry, applying a LUT Library
entry routes into StackPanel.add_lut_stage_from_library_entry, and
applying an Image Library entry routes into SampleImagePanel's
Sample/Reference slots (see image_library_panel.py's docstring) -- all
exactly like opening a session does -- none of these panels has a
reference to StackPanel or SampleImagePanel, MainWindow wires all of them
together (self.stack_memories_panel.recall_requested ->
self.stack_panel.set_stack, self.cdl_library_panel.apply_requested ->
self.stack_panel.apply_cdl_entry, self.stack_panel.cdl_save_requested ->
self.cdl_library_panel.save_entry, self.lut_library_panel.apply_requested
-> self.stack_panel.add_lut_stage_from_library_entry,
self.image_library_panel.load_as_sample_requested/
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
button as before (Image Library/LUT Library/CDL Library/Stack
Memories/Viewer/Stack/Scopes/Graph) -- reuses the exact same
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

FLOATING (per Tom, Cmd+Shift+1 through Cmd+Shift+0, same digits as each
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
StackPanel object can hand out two separately-placeable widgets like this,
and for where the Grid Size/Domain settings live now (a popup dialog
launched from a button in the stage list, not inline in Stack Params
anymore). See stack.py's module docstring for the full architecture and
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
import base64
import sys
from functools import partial
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDockWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QFileDialog, QMessageBox, QLabel, QCheckBox,
    QDialog, QTextBrowser, QDialogButtonBox, QToolButton, QStyle, QColorDialog,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QAction, QKeySequence, QColor
from PySide6.QtCore import Qt, QTimer, QByteArray, QEvent

from browser_panel import BrowserPanel
from stack_memories_panel import StackMemoriesPanel
from cdl_library_panel import CDLLibraryPanel
from lut_library_panel import LUTLibraryPanel
from image_library_panel import ImageLibraryPanel
from sample_image_panel import SampleImagePanel
from stack_panel import StackPanel
from scopes_panel import ScopesPanel
from lut_viewer_3d import LutViewer3D
from panel_frame import PanelFrame
from style import STYLESHEET, build_dark_palette, get_accent_color, set_accent_color
from clf_adapter import colour_lut3d_to_clf_node, process_list_wrapping
from format_writers import write_3dl, write_truelight_cub
from app_logic import export_image_array_to_file
import session_io
import workspace_io
import stack as st
from stack_memory import StackMemoryLibrary
from cdl_library import CDLLibrary
from lut_library import LUTLibrary
from image_library import ImageLibrary
from undo_history import UndoHistory
from help_text import USER_MANUAL_MARKDOWN
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

# (attribute name to store the QAction under, button label, target DOCK
# attribute name, default visible on startup). "_frame" is now a
# QDockWidget instance (see _make_dock), not a PanelFrame -- the name is
# kept from before the docking migration purely to avoid renaming every
# external reference (tests, GROUP-splitter-era code) for no functional
# reason; PanelFrame is still used, just as the dock's inner border/
# background only (see _make_dock/_build_dock_title_bar).
#
# All ten toggles live in the Workspace menu (see this module's
# docstring) -- LEFT/CENTER/RIGHT here just group them roughly matching
# each panel's default column (Browser+Image Library+LUT Library+CDL
# Library+Stack Memories=left column, Viewer+Stack=center column, Stack
# Params+Scopes+3D Viewer=right column) -- purely a DEFAULT starting
# position now (see _build_default_dock_layout), not a structural
# constraint the way the old splitters were: any panel can be dragged
# anywhere at runtime. PANEL_TOGGLES (the concatenation of all three) is
# used wherever the grouping doesn't matter (shortcut-digit assignment,
# building every dock). All default to visible on launch (per Tom) so
# nothing needs a first-run toggle click to appear.
#
# Position in this list is still what determines each panel's Shift+N /
# Cmd+Shift+N shortcut digit (see _build_menu_bar's docstring on the
# Shift+0 exception past position 9) -- unchanged by the docking
# migration, still matches USER_MANUAL.md/help_text.py's shortcut table.
PANEL_TOGGLES_LEFT = [
    ("browser_toggle", "Browser", "browser_frame", True),
    ("image_library_toggle", "Image Library", "image_library_frame", True),
    ("lut_library_toggle", "LUT Library", "lut_library_frame", True),
    ("cdl_library_toggle", "CDL Library", "cdl_library_frame", True),
    ("stack_memories_toggle", "Stack Memories", "stack_memories_frame", True),
]
PANEL_TOGGLES_CENTER = [
    ("sample_image_toggle", "Viewer", "sample_image_frame", True),
    ("stack_toggle", "Stack", "stack_frame", True),
]
PANEL_TOGGLES_RIGHT = [
    ("stack_params_toggle", "Parameters", "stack_params_frame", True),
    ("scopes_toggle", "Scopes", "scopes_frame", True),
    ("viewer_3d_toggle", "Graph", "viewer_3d_frame", True),
]
PANEL_TOGGLES = PANEL_TOGGLES_LEFT + PANEL_TOGGLES_CENTER + PANEL_TOGGLES_RIGHT

# Only window_size now -- panel visibility/position/floating all live in
# the dock layout itself (see _build_default_dock_layout for the DEFAULT
# arrangement, get_workspace_state/load_workspace_state for how an
# arbitrary drag-built arrangement is captured/restored via QMainWindow's
# own saveState()/restoreState()). Before the docking migration this dict
# also carried "panels"/"splitters"/"floating" keys mirroring
# get_workspace_state()'s old hand-rolled shape -- gone now that shape
# itself is gone (see this module's docstring, WORKSPACE PERSISTENCE).
DEFAULT_WORKSPACE_STATE = {
    "window_size": [1200, 800],
}


class _PanelDockWidget(QDockWidget):
    """QDockWidget subclass with exactly one behavior change: closing a
    FLOATING panel via its native titlebar close button HIDES it (stays
    floating internally, just not visible) instead of trying to redock it.

    HISTORY (kept for context -- #58 through #60 all tried variations of
    "redock it correctly on close" before Tom, on RUNNING.md #61, asked
    for this simpler behavior instead): #58 fixed a stray-placement crash
    via allowedAreas; #59's attempted follow-up (tracking a coarse
    Qt.DockWidgetArea per panel) turned out to be built on a false premise
    -- every panel in this app's single nested split-tree reports the
    SAME dockWidgetArea() regardless of real position, so that "fix"
    wasn't fixing anything; #60 replaced it with QMainWindow.saveState()/
    restoreState(), confirmed to reproduce a panel's exact pixel position
    -- genuinely correct when it applies, but Tom found a NEW failure mode
    on real hardware: if `_last_all_docked_state` doesn't fully agree with
    the window's current state (or more than one panel is floating,
    where #60 deliberately declined to use it at all -- see #60's own
    entry), restoreState() can silently no-op, leaving the panel exactly
    where it was: still floating, unresponsive to the X button, per Tom
    "if home is lost it refuses to go anywhere." Rather than keep
    patching an ever-growing set of "redock correctly" edge cases, #61
    sidesteps the entire problem: closing a floating panel no longer
    tries to figure out where it goes at all, it just hides -- there's no
    "home" to lose, so there's nothing left to refuse.

    isFloating() stays True the whole time (deliberately not reset to
    False) -- re-showing the panel later (Workspace menu, its shortcut)
    brings it back exactly as a floating window in its last position,
    consistent with "closing it didn't change anything about its
    dock/float state, only its visibility," the same relationship a
    DOCKED panel's close already has to its own dockedness (see below).
    `_floating_panels`/the Float-menu-checkbox bookkeeping in
    _on_dock_top_level_changed is untouched by this (no topLevelChanged
    fires from a plain hide) -- both correctly continue reporting this
    panel as floating, which it still is.

    Docked panels are unaffected -- closeEvent only intervenes while
    isFloating() is True; a docked panel's close (via the title bar's own
    close button, added per Tom -- see _build_dock_title_bar; Qt's native
    DockWidgetClosable X was never part of this custom title bar, but this
    button calls the same dock.close() Qt's own X would) falls through to
    the normal hide behavior, same as `toggleViewAction` unchecking it --
    that path was always reachable via the Workspace menu/shortcut even
    before this button existed, this is just a second, on-panel way in.
    RUNNING.md #61 makes the floating case behave the same way in this
    one specific respect (X hides, doesn't reposition), while leaving the
    two states otherwise exactly as different as they've always been
    (docked-and-hidden vs. floating-and-hidden are still tracked
    separately, per isFloating()).

    MainWindow._dock_panel (the Workspace menu's Float-uncheck path, and
    _dock_all_floating_panels' session-load/app-quit/reset-to-default
    safety net) is UNCHANGED -- still uses #60's saveState()/restoreState()
    redock logic. Tom's ask was specifically about the native X button,
    not about those other, deliberate ways to redock a panel."""

    def closeEvent(self, event):
        if self.isFloating():
            event.ignore()
            self.setVisible(False)
        else:
            super().closeEvent(event)


def _build_dock_title_bar(dock, title, corner_widget=None):
    """Custom title bar widget for a panel's QDockWidget -- reuses the
    EXACT #panelTitleRow/#panelTitle object names style.py already styles
    for PanelFrame's own (now-unused-for-docks) title row, so the visual
    language is unchanged even though PanelFrame itself no longer draws a
    title for panels built this way (see _make_dock -- PanelFrame is
    still used underneath, purely for the border/background; its own
    title-row capability was removed entirely 2026-07-25, see
    panel_frame.py). corner_widget (optional) is the SAME "..." menu
    button each panel already builds (self.viewer_3d.menu_button etc.) --
    placed on the same row as the title, right-aligned, same spot it sat
    in PanelFrame's own title row before this migration.

    Close button (per Tom, added after the fact -- originally this row
    was just the label + corner_widget, with float/close reachable only
    via the Workspace menu/shortcuts, since replacing Qt's native title
    bar with this custom one also silently threw away its native float/
    close buttons; see _PanelDockWidget's docstring). Built as a plain
    QToolButton rather than relying on QDockWidget's own ::close-button
    QSS subcontrol -- an ordinary button is fully stylable with this
    file's existing QToolButton/QPushButton rules and sits in this same
    custom row next to corner_widget, where a native subcontrol button
    (tied to Qt's own title bar) couldn't. Its icon comes from the
    current QStyle's standard set (SP_TitleBarCloseButton) -- the same
    icon Qt's own native title bar button would have used, so this
    doesn't invent new iconography, just relocates/re-implements it.
    `dock` (the QDockWidget this title bar belongs to) needs to be
    passed in now, unlike before, so this button has something to call
    close() on.

    NO float button here (per Tom, removed): dragging a panel by this
    title bar already floats it via Qt's own native drag-to-float, and
    the float button that used to sit here called dock.setFloating()
    directly, bypassing _float_panel/_dock_panel's _reparent_gl_safely
    wrapper (which suspends GL paint delivery during the transition and
    triggers _rebuild_gl_content_for_panel afterward) -- exactly the
    protection a GL-backed panel (the 3D Viewer) needs to not render
    broken after a float/dock transition. The Workspace menu's per-panel
    Float checkbox and the Cmd+Shift+<N> shortcuts (_make_float_action)
    still exist and still go through that safe wrapper -- only this
    redundant, unsafe title-bar shortcut to the same action is gone.

    WA_StyledBackground (per Tom, real bug caught on real hardware --
    RUNNING.md #54) -- a plain QWidget does NOT clip its background to a
    QSS border-radius by default; only QFrame (see PanelFrame's
    setFrameShape(StyledPanel)) paints its stylesheet background/border
    in a radius-aware way out of the box. Without this attribute,
    row_widget still painted its full SQUARE auto-fill background
    underneath the rounded border line style.py's #panelTitleRow rule
    draws, so the actual corners showed as a square patch of palette(base)
    poking out past the rounded outline -- exactly the "extra gray on the
    corners" artifact, invisible before this pass only because
    panelTitleRow never had a border-radius to clip to until RUNNING.md
    #53. Setting this forces Qt to paint the ENTIRE background through
    the style/stylesheet system, radius and all, same as QFrame already
    does natively."""
    row_widget = QWidget()
    row_widget.setObjectName("panelTitleRow")
    row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    row = QHBoxLayout(row_widget)
    row.setContentsMargins(8, 4, 8, 4)
    label = QLabel(title)
    label.setObjectName("panelTitle")
    row.addWidget(label, stretch=1)

    close_button = QToolButton()
    close_button.setObjectName("dockCloseButton")
    close_button.setAutoRaise(True)
    close_button.setToolTip("Close")
    close_button.setIcon(
        row_widget.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
    # dock.close() -- NOT dock.hide() -- so this goes through
    # _PanelDockWidget's own closeEvent override exactly like Qt's native
    # close button would have: redocks if floating, hides (in sync with
    # toggleViewAction/the Workspace menu) if already docked.
    close_button.clicked.connect(dock.close)
    row.addWidget(close_button)

    if corner_widget is not None:
        row.addWidget(corner_widget)
    return row_widget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LUTier")
        self.resize(*DEFAULT_WORKSPACE_STATE["window_size"])

        self._current_lut = None  # last baked stack result -- see _on_stack_changed
        self._session_path = None  # currently open .ltr file, or None (unsaved) -- see _save_session

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

    def _make_dock(self, frame_attr, label, content_widget, corner_widget=None):
        """Build one panel's QDockWidget -- wraps content_widget in a
        border-only PanelFrame for visual continuity, gives
        it a custom title bar (see _build_dock_title_bar) carrying the
        label and, if given, the panel's own "..." menu button, and wires
        `topLevelChanged` to `_on_dock_top_level_changed` so a float/dock
        transition is tracked correctly no matter what triggers it (menu,
        shortcut, or a user drag -- see this module's docstring).
        Stores the result on self under `frame_attr` (e.g.
        self.browser_frame) and also returns it, matching this file's
        existing "self.X_frame = ..." call-site style from before the
        docking migration."""
        dock = _PanelDockWidget(label, self)
        dock.setObjectName(frame_attr)
        # Plain Python attribute (not a Qt property) -- RUNNING.md #60,
        # so _PanelDockWidget.closeEvent can tell whether IT is the only
        # currently-floating panel without MainWindow having to hand it a
        # frame_attr string at every call site. See closeEvent's docstring.
        dock._frame_attr = frame_attr
        # WA_StyledBackground + QSS #52/#53/#54 aftermath (RUNNING.md #55) --
        # the real, composited bug Tom kept seeing wasn't panelTitleRow's
        # own corners at all (that part was already fixed by #54) -- it
        # was QMainWindow's own herringbone-textured background bleeding
        # through the QDockWidget's bare rectangular cell wherever
        # panelTitleRow/panelFrame's rounded corners recede from it (every
        # outer corner, plus the deliberate 4px gap between the two). A
        # widget.grab() of just the dock in isolation (what this session's
        # earlier headless verification used) fills that recess with a
        # neutral default instead of actually compositing what's behind it
        # in the real window, which is exactly why that verification
        # missed this -- confirmed by re-grabbing the FULL MainWindow
        # instead and reproducing the identical artifact Tom described.
        # Giving the dock itself an opaque palette(base) backing (see
        # style.py's QDockWidget rule) turns that recess into a solid
        # color matching the card content around it instead of textured
        # bleed-through -- QDockWidget needs WA_StyledBackground for the
        # same reason panelTitleRow did (#54): it's not a QFrame, so it
        # won't paint a QSS background-color without this set explicitly.
        dock.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        dock.setTitleBarWidget(_build_dock_title_bar(dock, label, corner_widget))
        dock.setWidget(PanelFrame(content_widget))
        dock.topLevelChanged.connect(partial(self._on_dock_top_level_changed, frame_attr))
        setattr(self, frame_attr, dock)
        return dock

    def _snapshot_all_docked_state(self):
        """Records a saveState() blob of the WHOLE window into
        _last_all_docked_state (RUNNING.md #60, superseding #59's
        per-panel dockWidgetArea() tracking -- see _PanelDockWidget.
        closeEvent's docstring for why that approach turned out to be
        fundamentally too coarse: every panel in this app's single nested
        split-tree reports the SAME dockWidgetArea(), regardless of which
        column it actually lives in). Called once right after the default
        layout is (re)built from scratch (startup, Workspace > Reset to
        Default) AND once after a remembered/imported workspace's saved
        dock state is actually applied (_apply_dock_state) -- neither
        addDockWidget/splitDockWidget's initial placement NOR
        restoreState() fires topLevelChanged for a widget that stays
        docked throughout (that signal only fires on an actual
        floating-state TRANSITION), so without an explicit call at each of
        these "the real layout just changed out from under any earlier
        snapshot" points, _last_all_docked_state would silently keep
        describing a superseded arrangement. Every subsequent successful
        return-to-fully-docked keeps this current via
        _on_dock_top_level_changed instead -- see that method."""
        self._last_all_docked_state = bytes(self.saveState())

    def _build_default_dock_layout(self):
        """(Re)establish the DEFAULT spatial arrangement (see this
        module's docstring for the ASCII diagram) using QMainWindow's own
        addDockWidget/splitDockWidget/resizeDocks -- called once from
        _build_layout at startup, and again by _reset_workspace_to_default
        (per Tom's "Reset to Default Workspace" menu entry, unchanged in
        spirit from before the docking migration). Purely a STARTING
        position now, not a structural constraint -- every dock can be
        dragged anywhere afterward.

        Ordering matters: the three-column split happens FIRST, via one
        representative dock per column (Browser/Viewer/Parameters) --
        THEN each column's remaining members are stacked vertically
        underneath their column's own anchor. Splitting vertically
        BEFORE the three-column horizontal split would stack the whole
        window's height instead of just one column's width (confirmed
        empirically in this sandbox) -- this ordering is what confines
        each vertical split to its own column."""
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.browser_frame)
        self.splitDockWidget(self.browser_frame, self.sample_image_frame, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.sample_image_frame, self.stack_params_frame, Qt.Orientation.Horizontal)

        # left column: Browser / Image Library / LUT Library / CDL
        # Library / Stack Memories, top to bottom.
        self.splitDockWidget(self.browser_frame, self.image_library_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.image_library_frame, self.lut_library_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.lut_library_frame, self.cdl_library_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.cdl_library_frame, self.stack_memories_frame, Qt.Orientation.Vertical)

        # center column: Viewer / Stack, top to bottom.
        self.splitDockWidget(self.sample_image_frame, self.stack_frame, Qt.Orientation.Vertical)

        # right column: Parameters / Scopes / Graph, top to bottom.
        self.splitDockWidget(self.stack_params_frame, self.scopes_frame, Qt.Orientation.Vertical)
        self.splitDockWidget(self.scopes_frame, self.viewer_3d_frame, Qt.Orientation.Vertical)

        # Same proportions the old splitters' setSizes() calls used --
        # resizeDocks is the QMainWindow-dock equivalent, taking a list of
        # docks sharing one axis and the sizes to give them along it.
        self.resizeDocks(
            [self.browser_frame, self.sample_image_frame, self.stack_params_frame],
            [260, 700, 340], Qt.Orientation.Horizontal)
        self.resizeDocks(
            [self.browser_frame, self.image_library_frame, self.lut_library_frame,
             self.cdl_library_frame, self.stack_memories_frame],
            [300, 180, 180, 180, 180], Qt.Orientation.Vertical)
        self.resizeDocks(
            [self.sample_image_frame, self.stack_frame],
            [260, 540], Qt.Orientation.Vertical)
        self.resizeDocks(
            [self.stack_params_frame, self.scopes_frame, self.viewer_3d_frame],
            [300, 350, 350], Qt.Orientation.Vertical)

    def _build_layout(self):
        # No central widget -- every panel is a QDockWidget (see
        # _make_dock), so the dock area is free to occupy the ENTIRE
        # window rather than surrounding a fixed centerpiece. Nesting
        # enabled so a column can itself be split further (see
        # _build_default_dock_layout, and any future drag the user makes)
        # rather than being limited to one split per edge.
        self.setDockNestingEnabled(True)

        # StackPanel built first (before the left column) since the
        # Stack Memories panel needs a live reference to it
        # (set_stack_getter) at construction time -- nothing about
        # building StackPanel itself depends on anything built after it.
        self.stack_panel = StackPanel()
        self.stack_panel.stack_changed.connect(self._on_stack_changed)

        # ---- left column: Browser (top) / Image Library / LUT Library / -
        # ---- CDL Library / Stack Memories (bottom) -----------------------
        # Five separate panels now (per Tom, Image Library inserted between
        # Browser and LUT Library) -- used to be one Browser PanelFrame with
        # an internal "Files" / "Stack Memories" QTabWidget, split into
        # their own stacked group so all five are visible at once instead
        # of one-tab-at-a-time. "Files" is the unchanged filesystem LUT
        # browser, "Image Library" (between Browser and LUT Library, per
        # Tom) is a shelf of reference images ingested into session memory
        # -- see image_library_panel.py/image_library.py -- "LUT Library"
        # is a shelf of LUTs ingested into session memory -- see
        # lut_library_panel.py/lut_library.py -- "CDL Library" is a shelf
        # of named, saved ASC CDL grades (see cdl_library_panel.py/
        # cdl_library.py), "Stack Memories" is a shelf of named, saved
        # Stack snapshots you can recall/update/rename/delete/export/
        # import -- all four libraries ride along with the session file
        # (see session_io.py).
        self.browser = BrowserPanel()
        # Loading a file or Identity LUT from the Browser ADDS a stage to
        # the Stack instead of replacing "the current LUT" -- there's no
        # separate current-LUT slot anymore, see stack_panel.py.
        self.browser.file_selected.connect(lambda path: self.stack_panel.add_lut_stage_from_path(path))
        self.browser.identity_lut_requested.connect(lambda size: self.stack_panel.add_identity_stage(size))
        self._make_dock("browser_frame", "Browser", self.browser)

        # Image Library's two apply_requested signals get connected to
        # self.sample_image once that's built further below (SampleImagePanel
        # doesn't exist yet at this point in the method).
        self.image_library_panel = ImageLibraryPanel()
        self._make_dock(
            "image_library_frame", "Image Library", self.image_library_panel,
            corner_widget=self.image_library_panel.menu_button)

        self.lut_library_panel = LUTLibraryPanel()
        # Applying an entry hands back the LUTLibraryEntry itself (already
        # holding the fully-loaded LUT, no disk read here) --
        # StackPanel.add_lut_stage_from_library_entry adds a new stage
        # from it directly, tagged so session_io.py embeds its table
        # rather than a file path (see that method's docstring). This
        # panel has no idea StackPanel exists, same arrangement as CDL
        # Library/Stack Memories.
        self.lut_library_panel.apply_requested.connect(self.stack_panel.add_lut_stage_from_library_entry)
        # corner_widget=self.lut_library_panel.menu_button -- Import.../
        # Apply to Stack/Rename.../Delete moved into the "..." header menu
        # (per Tom), same treatment as Viewer/Stack/Scopes.
        self._make_dock(
            "lut_library_frame", "LUT Library", self.lut_library_panel,
            corner_widget=self.lut_library_panel.menu_button)

        self.cdl_library_panel = CDLLibraryPanel()
        # Applying an entry hands back nothing itself -- it emits the
        # CDLEntry, and StackPanel.apply_cdl_entry decides whether to
        # update the currently-selected 'cdl' stage in place or add a new
        # one (see that method's docstring). This panel has no idea
        # StackPanel exists, same arrangement as Stack Memories' recall.
        self.cdl_library_panel.apply_requested.connect(self.stack_panel.apply_cdl_entry)
        # The reverse direction -- saving FROM the CDL inspector's "Save
        # to CDL Library..." button (see stack_panel.py) -- routes back
        # into this panel the same way; StackPanel has no idea
        # CDLLibraryPanel exists either.
        self.stack_panel.cdl_save_requested.connect(self.cdl_library_panel.save_entry)
        # Double-clicking a CDL file (.cc/.cdl/.ccc) in the Browser routes
        # here too (see browser_panel.py's docstring on why CDL files land
        # in the Library rather than going straight to the Stack the way
        # LUT files do) -- same import_from_path() the "..." menu's
        # Import... action and drag-and-drop both already use.
        self.browser.cdl_file_selected.connect(self.cdl_library_panel.import_from_path)
        # corner_widget=self.cdl_library_panel.menu_button -- same "..."
        # header-menu treatment as LUT Library above.
        self._make_dock(
            "cdl_library_frame", "CDL Library", self.cdl_library_panel,
            corner_widget=self.cdl_library_panel.menu_button)

        self.stack_memories_panel = StackMemoriesPanel()
        self.stack_memories_panel.set_stack_getter(self.stack_panel.get_stack)
        # Recalling a memory hands back a ready-to-use (already-copied)
        # Stack -- this panel has no idea StackPanel exists, MainWindow is
        # what wires the two together, same arrangement as opening a
        # session.
        self.stack_memories_panel.recall_requested.connect(self.stack_panel.set_stack)
        # corner_widget=self.stack_memories_panel.menu_button -- same "..."
        # header-menu treatment as LUT Library/CDL Library above.
        self._make_dock(
            "stack_memories_frame", "Stack Memories", self.stack_memories_panel,
            corner_widget=self.stack_memories_panel.menu_button)

        # ---- center column: Viewer (top) / Stack (main area) -----------
        self.sample_image = SampleImagePanel()
        # corner_widget=self.sample_image.menu_button -- the "..." menu now
        # sits on the SAME row as the "Viewer" title (per Tom) instead of
        # its own row underneath; see panel_frame.py / sample_image_panel.py.
        self._make_dock(
            "sample_image_frame", "Viewer", self.sample_image,
            corner_widget=self.sample_image.menu_button)
        # SampleImagePanel previews the live stack directly (no baking --
        # exact per-pixel apply, see stack.apply_stack_to_image_array),
        # re-rendering on every stack_changed the same way it used to for
        # ConcatenatePanel's chain_changed.
        self.sample_image.set_stack_getter(self.stack_panel.get_stack)
        self.stack_panel.stack_changed.connect(self.sample_image.refresh_preview)
        # Bundled default reference still, loaded Unmanaged (per Tom, same
        # default every image gets) but with its declared source color
        # space pre-filled in the Adapt dialog -- see
        # SampleImagePanel.restore_default_image (same method backs the
        # "..." menu's Default action, so a missing asset degrades the
        # same way here as it would from that menu).
        self.sample_image.restore_default_image()

        # Image Library entries "apply" into one of the Viewer's two
        # image slots rather than adding a Stack stage (see
        # image_library_panel.py's docstring) -- this panel has no idea
        # SampleImagePanel exists, MainWindow wires the two together, same
        # arrangement as every other library's apply_requested. Deferred to
        # here (rather than right after image_library_panel is built, up in
        # the left column above) since self.sample_image doesn't exist
        # until this point in the method.
        self.image_library_panel.load_as_sample_requested.connect(
            self.sample_image.load_sample_from_library_entry)
        self.image_library_panel.load_as_reference_requested.connect(
            self.sample_image.load_reference_from_library_entry)

        # corner_widget=list_menu_button -- Add Printer Lights / Add ACES
        # DRT / Edit Size-Domain consolidated into one header menu (per
        # Tom), replacing the old always-visible button row -- see
        # StackPanel._build_list_menu_button.
        self._make_dock(
            "stack_frame", "Stack", self.stack_panel.list_widget_area,
            corner_widget=self.stack_panel.list_menu_button)

        # ---- right column: Stack Params / Scopes / Graph ---------------
        # Stack Params at top (it's the one you reach for most, editing
        # whatever's selected in the Stack); Scopes in the middle; Graph
        # at the bottom (per Tom -- default starting position only now,
        # see _build_default_dock_layout; freely rearrangeable afterward).
        self._make_dock("stack_params_frame", "Parameters", self.stack_panel.params_widget_area)

        self.scopes_panel = ScopesPanel()
        # corner_widget=scopes_panel.menu_button -- the old "Scope:" combo
        # is now the same "..." header-menu pattern as Viewer/Stack (per
        # Tom), see scopes_panel.py's _build_menu_button.
        self._make_dock(
            "scopes_frame", "Scopes", self.scopes_panel,
            corner_widget=self.scopes_panel.menu_button)
        # Scopes read whatever SampleImagePanel is currently previewing
        # (post-stack) -- same source of truth as the wipe compare, not a
        # separate re-derivation.
        self.sample_image.after_image_changed.connect(self.scopes_panel.set_image_array)
        self.scopes_panel.set_image_array(self.sample_image.get_current_after_array())

        # Graph -- its own panel again (see scopes_panel.py's docstring).
        # Renamed from "3D Viewer" per Tom, once LutViewer3D grew a 1D
        # YRGB-curve mode alongside the original 3D point cloud (see
        # lut_viewer_3d.py's docstring) -- "3D Viewer" stopped describing
        # what the panel actually shows. Internal names (viewer_3d,
        # viewer_3d_frame, viewer_3d_toggle, LutViewer3D) are unchanged --
        # only the user-facing label moved, renaming every internal
        # reference wasn't worth the churn.
        #
        # Has a "..." corner menu (per Tom's earlier follow-up -- the view
        # switch moved from an on-panel button pair into a menu, same
        # convention Viewer/Stack/Scopes already use, see
        # LutViewer3D.populate_menu) -- Grid density/Cage opacity stay
        # inline in LutViewer3D's own layout, only view selection moved.
        self.viewer_3d = LutViewer3D()
        self._make_dock(
            "viewer_3d_frame", "Graph", self.viewer_3d,
            corner_widget=self.viewer_3d.menu_button)
        # Reads the baked stack LUT directly (see _on_stack_changed below)
        # -- independent of the sample image entirely, unlike Scopes.

        # Now that every dock exists, lay them out in the default spatial
        # arrangement (see _build_default_dock_layout's own docstring) --
        # purely a starting position, not a structural constraint.
        self._build_default_dock_layout()
        self._snapshot_all_docked_state()

        # Export lives in its own dialog now (File > Export..., ⌘E, per
        # Tom) rather than an always-visible bottom-bar row -- see
        # _show_export_dialog. self.format_combo is built there instead of
        # here now.

        # Apply default visibility -- must happen after every dock above
        # exists.
        for _btn_attr, _label, frame_attr, default_visible in PANEL_TOGGLES:
            getattr(self, frame_attr).setVisible(default_visible)

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
        "..." corner menu -- LUT Library, CDL Library, Stack Memories,
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
        then Shift+0 for
        the TEN Workspace panel toggles, assigned by simple position in
        PANEL_TOGGLES (Browser=1, Image Library=2, LUT Library=3, CDL
        Library=4, Stack Memories=5, Viewer=6, Stack=7, Parameters=8,
        Scopes=9, Graph=10) -- set directly on each dock's own
        toggleViewAction() below. Extrapolating a new panel's shortcut is
        automatic (just its position in the list), not something to
        hand-assign each time a panel's added/reordered -- EXCEPT past
        position 9, where keyboards run out of single digits: position 10
        (Graph) gets Shift+0 rather than the nonsensical "Shift+10",
        following the ordinary keyboard-row convention of 0 coming right
        after 9. If an 11th panel is ever added, this scheme has no more
        room and would need an actual redesign (e.g. Cmd+1..9,0 instead of
        Shift+), not just another special case.

        FLOATING (per Tom): Cmd+Shift+1 through Cmd+Shift+9 then
        Cmd+Shift+0 -- the exact same per-panel digit as its Shift+N
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
        new_action = file_menu.addAction("New Session", self._new_session)
        new_action.setShortcut(QKeySequence("Ctrl+N"))
        open_action = file_menu.addAction("Open Session...", self._open_session)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        file_menu.addSeparator()
        save_action = file_menu.addAction("Save Session", self._save_session)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_as_action = file_menu.addAction("Save Session As...", self._save_session_as)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        file_menu.addSeparator()
        # Export... (⌘E, per Tom) -- opens _show_export_dialog rather than
        # exporting straight away, same "menu entry, not an inline action"
        # shape as everything else in this menu; see that method's
        # docstring for why this replaced the old always-visible bottom
        # bar row.
        export_action = file_menu.addAction("Export...", self._show_export_dialog)
        export_action.setShortcut(QKeySequence("Ctrl+E"))

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

        # All ten show/hide toggles together first, THEN all ten Float
        # toggles together (per Tom -- this used to interleave each
        # panel's pair; two flat blocks read as two distinct KINDS of
        # control instead). Both loops walk PANEL_TOGGLES in the exact
        # same order, so a given row position means the same panel in
        # either block.
        for i, (action_attr, label, frame_attr, _default_visible) in enumerate(PANEL_TOGGLES, start=1):
            # Position 10 (currently Graph) gets Shift+0, not the
            # unparseable "Shift+10" -- see this method's docstring.
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

        # ---- category menus -- see docstring above ------------------------
        image_library_menu = menu_bar.addMenu("Image Library")
        self.image_library_panel.populate_menu(image_library_menu)

        lut_library_menu = menu_bar.addMenu("LUT Library")
        self.lut_library_panel.populate_menu(lut_library_menu)

        cdl_library_menu = menu_bar.addMenu("CDL Library")
        self.cdl_library_panel.populate_menu(cdl_library_menu)

        stack_memories_menu = menu_bar.addMenu("Stack Memories")
        self.stack_memories_panel.populate_menu(stack_memories_menu)

        viewer_menu = menu_bar.addMenu("Viewer")
        self.sample_image.populate_menu(viewer_menu)

        stack_menu = menu_bar.addMenu("Stack")
        self.stack_panel.populate_menu(stack_menu)

        scopes_menu = menu_bar.addMenu("Scopes")
        self.scopes_panel.populate_menu(scopes_menu)

        graph_menu = menu_bar.addMenu("Graph")
        self.viewer_3d.populate_menu(graph_menu)

        # ---- Help menu: Manual (per Tom) -- see _show_manual_dialog. Text
        # comes from help_text.USER_MANUAL_MARKDOWN, a plain Python string
        # constant, not a runtime read of USER_MANUAL.md -- see that
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

    def _new_session(self):
        if (self.stack_panel.get_stack().stages
                or self.stack_memories_panel.get_library().memories
                or self.cdl_library_panel.get_library().entries
                or self.lut_library_panel.get_library().entries
                or self.image_library_panel.get_library().entries):
            reply = QMessageBox.question(
                self, "New Session",
                "Discard the current stack, Stack Memories, CDL Library, "
                "LUT Library, and Image Library and start a new session? "
                "Unsaved changes will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        # Floating state was never part of "the session" (see
        # get_workspace_state) -- New Session doesn't touch workspace
        # layout otherwise, so this is the one place that needs its own
        # explicit call rather than inheriting it from load_workspace_state.
        self._dock_all_floating_panels()
        new_stack = st.Stack()
        self._restoring_stack_history = True
        try:
            self.stack_panel.set_stack(new_stack)
        finally:
            self._restoring_stack_history = False
        # Fresh history for this new, empty stack -- undo must not be able
        # to walk back into whatever the previous (now-discarded) session
        # had (per Tom: "reset on new/open session").
        self._undo_history = UndoHistory(new_stack.copy())
        self._update_undo_redo_actions()
        self.stack_memories_panel.load_library(StackMemoryLibrary())
        self.cdl_library_panel.load_library(CDLLibrary())
        self.lut_library_panel.load_library(LUTLibrary())
        self.image_library_panel.load_library(ImageLibrary())
        self.sample_image.restore_default_image()
        self._session_path = None

    def _open_session(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session", "",
            "LUTier Session (*.ltr *.lutier);;All files (*)",
        )
        if not path:
            return
        try:
            (new_stack, sample_image_state, memory_library, cdl_library,
             lut_library, image_library, undo_history_state,
             warnings) = session_io.load_session(path)
        except Exception as e:  # noqa: BLE001 -- surface any read/parse failure to the user
            QMessageBox.critical(self, "Failed to open session", str(e))
            return

        self._restoring_stack_history = True
        try:
            self.stack_panel.set_stack(new_stack)
        finally:
            self._restoring_stack_history = False
        # Reset undo history to this session's own -- either what it was
        # saved with (undo_history_state) or, for a file with none (no
        # history saved, or a pre-version-8 file), a fresh single-state
        # history for the stack just loaded. Either way, undo can no
        # longer walk back into whatever the PREVIOUS session had (per
        # Tom: "reset on new/open session but keep undo history inside of
        # project data if possible").
        if undo_history_state is not None:
            states, pointer = undo_history_state
            self._undo_history = UndoHistory.from_states(states, pointer)
        else:
            self._undo_history = UndoHistory(new_stack.copy())
        self._update_undo_redo_actions()
        self.stack_memories_panel.load_library(memory_library)
        self.cdl_library_panel.load_library(cdl_library)
        self.lut_library_panel.load_library(lut_library)
        self.image_library_panel.load_library(image_library)
        if sample_image_state is not None:
            self.sample_image.load_session_state(sample_image_state)
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
        try:
            session_io.save_session(
                path, self.stack_panel.get_stack(), self.sample_image.get_session_state(),
                stack_memory_library=self.stack_memories_panel.get_library(),
                cdl_library=self.cdl_library_panel.get_library(),
                lut_library=self.lut_library_panel.get_library(),
                image_library=self.image_library_panel.get_library(),
                undo_history=self._undo_history)
        except Exception as e:  # noqa: BLE001 -- surface any write failure to the user
            QMessageBox.critical(self, "Failed to save session", str(e))
            return
        self._session_path = path

    def _make_float_action(self, menu, action_attr, label, frame_attr, shortcut=None):
        """One checkable Workspace menu entry per panel that pops it out
        into its own top-level window instead of showing/hiding it in
        place -- see _build_menu_bar's docstring, FLOATING, and _float_
        panel/_dock_panel below. Always starts unchecked (nothing floats
        at launch, and _on_dock_top_level_changed keeps this correctly in
        sync afterward regardless of what actually triggers a float/dock
        transition -- this menu, the shortcut, or a drag)."""
        action = QAction(f"Float {label}", self)
        action.setCheckable(True)
        action.blockSignals(True)
        action.setChecked(False)
        action.blockSignals(False)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.toggled.connect(partial(self._on_panel_float_toggled, frame_attr))
        menu.addAction(action)
        setattr(self, action_attr, action)
        return action

    def _on_panel_float_toggled(self, frame_attr, checked):
        if checked:
            self._float_panel(frame_attr)
        else:
            self._dock_panel(frame_attr)

    def _reparent_gl_safely(self, frame, reparent_fn):
        """Runs `reparent_fn()` -- a `dock.setFloating(True)` call in
        _float_panel, or `dock.setFloating(False)` in _dock_panel -- with
        paint delivery suspended for every QOpenGLWidget currently living
        under `frame` (findChildren digs through nested layouts, e.g.
        LutViewer3D's own internal structure, so this isn't hardcoded to
        the Graph panel specifically -- any future panel that adds GL
        content gets the same protection for free).

        Only covers float/dock transitions THIS FILE triggers directly
        (the Workspace menu's Float checkbox, the Cmd+Shift+N shortcut) --
        since the docking migration, a panel can ALSO become floating by
        being dragged out interactively, which Qt performs internally
        with no hook available to suspend paint delivery beforehand. See
        this module's docstring for why that gap is an accepted,
        deliberately-documented risk rather than something this method
        can close.

        Per Tom's crash report: closing/redocking a floated panel
        segfaulted, traced to a paint event landing on the 3D Viewer's
        GLViewWidget mid-reparent, while its GL context was between
        states. setUpdatesEnabled(False) is Qt's own supported way to
        tell a widget "don't schedule or deliver paint events for
        yourself right now" -- covering the actual moment the crash's own
        stack trace showed a paint event racing the reparent. Paired with
        _SafeGLViewWidget's own isValid() guard in lut_viewer_3d.py (a
        second, independent line of defense at the point the GL calls
        would actually happen) rather than relying on either alone.

        update() after re-enabling forces one fresh repaint once the
        widget has actually settled in its new home, rather than leaving
        it showing a stale frame from before the reparent -- relevant to
        the OTHER half of Tom's report ("3D viewer was broken when
        floated"), though this alone may not be the full story there;
        flag if it's still not right after this.

        UNVERIFIED ON REAL HARDWARE -- see lut_viewer_3d.py's docstring."""
        gl_children = frame.findChildren(QOpenGLWidget)
        for widget in gl_children:
            widget.setUpdatesEnabled(False)
        try:
            reparent_fn()
        finally:
            for widget in gl_children:
                widget.setUpdatesEnabled(True)
                widget.update()

    def _rebuild_gl_content_for_panel(self, frame_attr):
        """Per-panel hook for panels whose GL content needs a full rebuild
        (not just an update()) after being floated or docked -- see
        lut_viewer_3d.LutViewer3D.rebuild_gl_view's docstring (RENDER FIX)
        for why _reparent_gl_safely's setUpdatesEnabled dance alone isn't
        enough for the 3D Viewer: the report was a genuinely blank/black
        view, not a stale or missed repaint, meaning the GL context itself
        needs to be recreated from scratch in the widget's final location,
        not just repainted.

        Only viewer_3d_frame has a hook right now -- deliberately a
        one-entry mapping rather than a generic 'every panel must define
        this' interface, since no other panel has GL content today. Add
        an entry here if that changes rather than growing a parallel
        mechanism elsewhere.

        Called from _on_dock_top_level_changed AFTER the transition has
        actually completed (Qt's own signal, fired regardless of what
        triggered it -- see this module's docstring) -- rebuilding any
        earlier would just mean doing it again once the widget settles
        into its real final state."""
        if frame_attr == "viewer_3d_frame":
            self.viewer_3d.rebuild_gl_view()

    def _float_panel(self, frame_attr):
        """Pop the panel at `frame_attr` out of the main window into its
        own floating top-level window -- a thin wrapper over
        QDockWidget.setFloating(True), Qt's own native mechanism (see
        this module's docstring for why this migration REPLACED the old
        hand-rolled setParent()+setWindowFlags() dance #39 built, rather
        than keeping it: confirmed empirically that QDockWidget's native
        floating already produces exactly Qt.WindowType.Tool, parented to
        MainWindow, with no WindowStaysOnTopHint -- the same end state #39
        built by hand, now free). _on_dock_top_level_changed (connected to
        every dock's `topLevelChanged` signal at construction, see
        _make_dock) is what actually updates `_floating_panels`/the Float
        menu checkbox and triggers `_rebuild_gl_content_for_panel` --
        keeping that in ONE place means it stays correct whether the
        float was triggered here, by the shortcut, or by a user drag."""
        dock = getattr(self, frame_attr)
        if dock.isFloating():
            return
        self._reparent_gl_safely(dock, lambda: dock.setFloating(True))

    def _dock_panel(self, frame_attr, rebuild_gl_content=True):
        """Inverse of _float_panel -- QDockWidget.setFloating(False).

        rebuild_gl_content=False suppresses the rebuild
        _on_dock_top_level_changed would otherwise trigger -- used by
        closeEvent's shutdown-time _dock_all_floating_panels call (see
        that method), where the app is about to quit and no one will ever
        see the rebuilt widget: creating a brand new QOpenGLWidget with
        zero benefit, at the single riskiest moment for GL work
        (mid-teardown), is pure downside. Every other caller keeps the
        default (rebuild normally -- the app keeps running afterward, so
        the render fix still matters there). Implemented via the
        `_suppress_gl_rebuild_on_dock_change` instance flag rather than a
        parameter threaded through the signal handler, since the signal
        itself carries no way to pass extra arguments through to
        `_on_dock_top_level_changed`.

        Called three ways: the Float menu action being unchecked,
        _PanelDockWidget's own closeEvent override (a floating panel's
        native titlebar close button -- see that class's docstring), and
        _dock_all_floating_panels (session load safety AND app-quit
        safety, see that method)."""
        dock = getattr(self, frame_attr)
        if not dock.isFloating():
            return
        # Same fix as _PanelDockWidget.closeEvent, same reason (RUNNING.md
        # #58) -- a panel floated without Shift held sits at
        # allowedAreas=NoDockWidgetArea (see #51/#55); redocking it via
        # setFloating(False) while that's still the case starves Qt of any
        # valid target area, so it comes back as a stray, badly-placed
        # widget instead of resuming its real layout slot. Reset to
        # AllDockWidgetAreas immediately before, not after -- the
        # topLevelChanged-driven reset in _on_dock_top_level_changed fires
        # too late to help THIS call, only the next one.
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        if not rebuild_gl_content:
            self._suppress_gl_rebuild_on_dock_change = True
        try:
            # RUNNING.md #59/#60 -- see _PanelDockWidget.closeEvent's
            # docstring for the full reasoning (same fix, same bug, this
            # is just the other of the two places that ever redock a
            # panel -- the Workspace menu's Float-uncheck path, and
            # _dock_all_floating_panels' session-load/app-quit/reset-to-
            # default safety net, all funnel through here). When this
            # panel is the ONLY one currently floating, restoring
            # _last_all_docked_state reproduces its exact pre-float
            # nested position (confirmed directly -- see closeEvent's
            # docstring for why the coarse per-area approach #59 tried
            # first couldn't). With more than one panel floating at once
            # (e.g. _dock_all_floating_panels looping through several),
            # restoring that blob could also pull back in whichever
            # panel(s) besides this one are still floating -- falls back
            # to plain setFloating(False) (#58's behavior) in that case
            # rather than risk that; _dock_all_floating_panels doesn't
            # need placement precision anyway (quit/reset-to-default both
            # discard or immediately overwrite the result).
            def _redock():
                only_floater = self._floating_panels <= {frame_attr}
                if only_floater and self._last_all_docked_state is not None:
                    self.restoreState(QByteArray(self._last_all_docked_state))
                else:
                    dock.setFloating(False)
            self._reparent_gl_safely(dock, _redock)
        finally:
            self._suppress_gl_rebuild_on_dock_change = False

    def _dock_all_floating_panels(self, rebuild_gl_content=True):
        """Force every currently-floating panel back into the main window
        -- called before applying a NEW workspace state (session Open/New,
        see load_workspace_state/_new_session) so the dock layout starts
        from a fully-docked baseline before saved state is applied on top.
        Floating state itself was never part of a saved SESSION in the
        first place (see get_workspace_state/session_io.py) -- this is
        purely about not leaving stray floating windows open from
        whatever was going on in the PREVIOUS session once a new one
        loads.

        rebuild_gl_content is passed straight through to each _dock_panel
        call -- closeEvent passes False here (see that method) since the
        app is quitting right after and rebuilding GL content nobody will
        ever see is pure risk. Every other caller keeps the default."""
        for frame_attr in list(self._floating_panels):
            self._dock_panel(frame_attr, rebuild_gl_content=rebuild_gl_content)

    def _on_dock_top_level_changed(self, frame_attr, floating):
        """Single source of truth for a panel's float/dock transition,
        connected to every dock's own `topLevelChanged` signal at
        construction (see _make_dock) -- fires for EVERY transition
        regardless of cause (the Workspace menu, the Cmd+Shift+N
        shortcut, or the user dragging the panel out/back in), which is
        exactly why this replaced the old eventFilter-based bookkeeping
        that only covered the paths this file triggered directly. Keeps
        `_floating_panels` correct, triggers
        `_rebuild_gl_content_for_panel` (skipped during app-quit teardown
        -- see `_suppress_gl_rebuild_on_dock_change`, set by _dock_panel),
        and syncs the Float menu checkbox so a drag-triggered float still
        shows up checked there, not just floats "invisibly" to the menu."""
        if floating:
            self._floating_panels.add(frame_attr)
        else:
            self._floating_panels.discard(frame_attr)
            # RUNNING.md #60 -- keep _last_all_docked_state current
            # whenever the window returns to fully-docked (the LAST
            # floating panel just redocked), regardless of what triggered
            # it (menu, shortcut, a user drag-to-redock, or the
            # restoreState()/setFloating(False) call _dock_panel/
            # closeEvent just made for their own redock -- harmless to
            # snapshot again here, it'll just re-read whatever state that
            # call actually settled into). Only when _floating_panels is
            # now EMPTY -- see _last_all_docked_state's docstring
            # (__init__) for why a mid-multi-float snapshot would be the
            # wrong thing to save (it would describe an arrangement with
            # some OTHER panel already yanked back in early).
            if not self._floating_panels:
                self._snapshot_all_docked_state()

        if not self._suppress_gl_rebuild_on_dock_change:
            self._rebuild_gl_content_for_panel(frame_attr)

        float_attr = self._panel_float_action_attr.get(frame_attr)
        if float_attr:
            float_action = getattr(self, float_attr)
            float_action.blockSignals(True)
            float_action.setChecked(floating)
            float_action.blockSignals(False)

        # Shift-to-dock override (see __init__'s installEventFilter call
        # and eventFilter below) -- a panel that JUST started floating
        # gets Qt's dock-preview/snap suppressed immediately (unless Shift
        # happens to already be held at that exact instant, e.g. Tom
        # started the drag with it down), rather than waiting for the
        # next keyPress/Release to establish the right starting state.
        # A panel that just got docked is reset back to Qt's own default
        # (AllDockWidgetAreas) -- once it's docked, allowedAreas only
        # matters again the NEXT time it's dragged out, and resetting here
        # (rather than leaving whatever Shift last left it at) keeps that
        # next float-out starting from a known, deliberate state instead
        # of inheriting a stale one from this transition.
        if floating:
            shift_held = bool(
                QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            getattr(self, frame_attr).setAllowedAreas(
                Qt.DockWidgetArea.AllDockWidgetAreas if shift_held
                else Qt.DockWidgetArea.NoDockWidgetArea)
        else:
            getattr(self, frame_attr).setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

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
        return {
            "window_size": [self.width(), self.height()],
            "dock_state_b64": base64.b64encode(bytes(self.saveState())).decode("ascii"),
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
        window_size = (workspace or {}).get("window_size")
        if window_size and len(window_size) == 2:
            try:
                self.resize(int(window_size[0]), int(window_size[1]))
            except (TypeError, ValueError):
                pass

        dock_state_b64 = (workspace or {}).get("dock_state_b64")
        if dock_state_b64:
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
        for _action_attr, _label, frame_attr, _default_visible in PANEL_TOGGLES:
            getattr(self, frame_attr).setVisible(True)
        self._build_default_dock_layout()
        self._snapshot_all_docked_state()
        self.resize(*DEFAULT_WORKSPACE_STATE["window_size"])

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

    def _on_stack_changed(self):
        """Single place that re-bakes the Stack and pushes the result to
        every panel that displays it -- fires on every stage add/remove/
        reorder/enable-toggle/opacity/param edit/grid-size/domain change
        (StackPanel.stack_changed), so 3D Viewer and export can't drift
        out of sync with what's actually in the stack. Sample Image
        previews the stack directly (see set_stack_getter above) rather
        than needing this baked result.

        Wrapped in try/except (per Tom's Stack Forking feature) because a
        stack can now pass through a transiently invalid structural state
        that only bake_stack_to_lut3d/_run_stages actually detects --
        most notably, deleting one half of a fork_split/fork_merge pair
        via that row's own "x" button (see stack_panel.py's
        _StageRowWidget.delete_button/_on_row_delete_clicked), which is
        an entirely ordinary, expected single-click action for every
        other stage kind but leaves an unmatched split or merge behind
        here until its other half is removed too. This fires on every
        single edit -- including live slider drags -- so a QMessageBox
        here would be far too disruptive; StackPanel.set_bake_error
        surfaces it inline instead (see its docstring) and this simply
        skips pushing a new bake, leaving the 3D Viewer/_current_lut at
        their last valid state rather than crashing the whole app over
        what's very likely a mid-edit, about-to-be-fixed structure."""
        try:
            baked = self.stack_panel.get_baked_lut()
        except Exception as e:  # noqa: BLE001 -- see docstring above
            self.stack_panel.set_bake_error(str(e))
            return
        self.stack_panel.set_bake_error(None)
        self._current_lut = baked
        self.viewer_3d.set_lut(baked)

    # ---- undo/redo (see undo_history.py for scope/design) ----------------

    def _on_stack_changed_for_history(self):
        """Records a new undo step for every genuine Stack edit --
        connected to the same StackPanel.stack_changed signal
        _on_stack_changed listens to, fired by every mutation (add/
        remove/reorder/enable/opacity/param edit, size/domain, recalling
        a Stack Memory, applying a CDL/LUT Library entry, opening a
        session, New Session -- see stack_panel.py's _emit_changed call
        sites). Skipped while _restoring_stack_history is set, i.e. while
        THIS method's own _undo()/_redo() is the one calling
        StackPanel.set_stack() -- otherwise restoring a past state would
        immediately record itself as a new edit, corrupting the redo
        tail. .copy() here is required, not just cheap insurance:
        StackPanel.get_stack() returns its own live, still-mutable Stack
        object, so storing it directly (rather than a copy) would let
        later edits silently rewrite history that's already been
        recorded."""
        if self._restoring_stack_history:
            return
        self._undo_history.push(self.stack_panel.get_stack().copy())
        self._update_undo_redo_actions()

    def _undo(self):
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
        self.undo_action.setEnabled(self._undo_history.can_undo)
        self.redo_action.setEnabled(self._undo_history.can_redo)

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
            # mid-gray hint-label color used elsewhere, e.g.
            # _update_domain_readout's own readout, was too low-contrast
            # to read comfortably in dark mode here). This is the same
            # palette(highlight) blue already used for stack_panel.py's
            # enabled-stage toggle/delete-button hover (see style.py's
            # QToolButton#stageEnabledToggle/#stageDeleteButton rules) --
            # an accent color Qt keeps legible against both light and
            # dark palettes, not a hardcoded hex that could fight either.
            self.export_specs_label.setStyleSheet("color: palette(highlight); font-size: 11px;")
            outer.addWidget(self.export_specs_label)

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
            self.export_still_to_library_checkbox = QCheckBox("Import Still to Image Library")
            self.export_still_to_library_checkbox.setEnabled(False)
            still_sub_row.addWidget(self.export_still_to_library_checkbox)
            outer.addLayout(still_sub_row)

            self.export_add_to_library_checkbox = QCheckBox("Import to LUT Library")
            outer.addWidget(self.export_add_to_library_checkbox)

            self.export_save_memory_checkbox = QCheckBox("Save Stack Memory")
            outer.addWidget(self.export_save_memory_checkbox)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
            export_button = button_box.addButton(
                "Export...", QDialogButtonBox.ButtonRole.AcceptRole)
            export_button.clicked.connect(self.export_current_lut)
            button_box.rejected.connect(dialog.close)
            outer.addWidget(button_box)

            self._export_dialog = dialog
        self.export_specs_label.setText(self._export_specs_text())
        show_floating(self._export_dialog)

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
        size, domain, and the stage list, read straight from the live
        Stack (self.stack_panel.get_stack()) rather than a fresh bake --
        all three are already plain attributes there (see stack.py), no
        need to actually bake a LUT3D just to preview what's about to be
        exported. The size/domain line's formatting matches Stack Params'
        own Grid Size/Domain readout exactly (see stack_panel.py's
        _update_domain_readout) -- same facts, so they should read
        identically wherever they're shown."""
        stack = self.stack_panel.get_stack()
        lo, hi = stack.domain_min, stack.domain_max
        if len(set(lo)) == 1 and len(set(hi)) == 1:
            domain_text = f"[{lo[0]:g}, {hi[0]:g}]"
        else:
            domain_text = (
                f"R[{lo[0]:g},{hi[0]:g}] "
                f"G[{lo[1]:g},{hi[1]:g}] "
                f"B[{lo[2]:g},{hi[2]:g}]"
            )
        lines = [f"{stack.size}³ · domain {domain_text}"]

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
        try:
            lut = self.stack_panel.get_baked_lut()
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
            extra_lines.append(f'Saved Stack Memory "{Path(out_path).stem}".')

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
        written still to ImageLibraryPanel.import_from_path, i.e. a real
        re-decode of the file on disk rather than the in-memory array --
        matches how every other Image Library import already works (see
        image_library.py's docstring on why entries there are path-
        referenced/re-decoded rather than fully embedded, unlike the LUT
        Library's import_lut_object above)."""
        if not self.sample_image.has_sample_image():
            return "Export Still skipped: no sample image loaded."
        still_path = str(Path(lut_out_path).with_suffix(".png"))
        try:
            result = self.sample_image.render_full_resolution_after(
                self.stack_panel.get_stack())
            export_image_array_to_file(result, still_path)
        except Exception as e:  # noqa: BLE001 -- report, don't block the rest of Export
            return f"Export Still failed: {e}"

        lines = [f"Exported a still to:\n{still_path}"]
        if self.export_still_to_library_checkbox.isChecked():
            self.image_library_panel.import_from_path(still_path)
            lines.append("Imported the still into the Image Library.")
        return "\n".join(lines)


def main():
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
    app = QApplication(sys.argv)
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
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
