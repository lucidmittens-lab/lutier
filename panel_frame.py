"""
panel_frame.py -- a bordered wrapper around a panel widget. Used so each
Workspace-menu-controlled section (Browser, Viewer, Parameters, Stack,
Scopes) reads as a distinct card rather than just loose widgets stacked in
a column, and so toggling a panel off hides its border along with its
content -- there's no way for a sub-element to stay visible while its
frame disappears, since everything lives inside one widget that gets one
setVisible() call.

Does not reparent or copy content_widget's internals -- just inserts the
existing widget into this frame's layout, so all external references
(MainWindow calling self.viewer_3d.set_lut(...), signal connections, etc.)
keep working exactly as before. Only the TOP-LEVEL visibility toggle in
MainWindow needs to target the frame instead of the raw widget.

SIMPLIFIED 2026-07-25 (see CLEANUP_AND_PERF_OBSERVATIONS_2026-07-25.md)
-- this class used to also build its own title row (a `title` string
label, an optional right-aligned `corner_widget`, both matching style.py's
`#panelTitleRow`/`#panelTitle` object names). That capability predated
the QMainWindow/QDockWidget docking migration, back when Browser held
Files/Stack Memories as tabs and PanelFrame was each panel's own title
bar. Every real panel's title bar has been built by main_window.py's
`_build_dock_title_bar` instead since that migration -- it's a QDockWidget
title bar, not something PanelFrame could provide anyway, and it reuses
the exact same `#panelTitleRow`/`#panelTitle` names so the visual result
is identical either way. `PanelFrame` itself has had exactly one caller
since (`main_window.py`'s `_make_dock`), always with no title and no
corner_widget -- so the title-row code was dead in the running app,
confirmed by walking the whole call graph, not just this file's own
history of docstring caveats saying so. Removed rather than left as a
"may well get used again" capability: reviving title-bar-in-a-QFrame
would need re-deriving from `_build_dock_title_bar`'s current behavior
anyway (WA_StyledBackground, the exact row layout) to actually match it,
so keeping a stale, unexercised copy around wasn't saving future work,
just accumulating it. `git`/session history has the removed version if
it's ever actually needed again.
"""
from PySide6.QtWidgets import QFrame, QVBoxLayout


class PanelFrame(QFrame):
    def __init__(self, content_widget, parent=None):
        super().__init__(parent)
        self.setObjectName("panelFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        # Tightened from (10, 8, 10, 10) per Tom -- panels read as having a
        # lot of dead space between their actual content and the panel
        # edge. This is one of two stacked sources of that: every panel's
        # OWN top-level layout also used to rely on Qt's default content
        # margins (a further ~9-11px per side on most styles) rather than
        # setting its own -- see each panel's __init__/_build_*_ui, now
        # zeroed out so this smaller margin here is the only gutter, not
        # doubled up with another layer's default. Not zero here: still
        # enough space that content doesn't visually clip into
        # PANEL_BORDER_RADIUS's rounded corner (style.py, 6px).
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(content_widget, stretch=1)
