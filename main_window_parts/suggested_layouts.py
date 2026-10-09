"""main_window_parts/suggested_layouts.py -- SUGGESTED workspace layouts
(2026-10-09): one-click arrangements of the panels for a kind of work,
like Resolve's Media and Color pages but NOT separate modes -- applying
one simply rearranges the one workspace (docks shown/hidden and placed),
which is then yours to change and is remembered as usual. Nothing tracks
"which layout you're in".

- MEDIA: the file Browser, the Source viewer and Media Libraries
  (spanning the bottom) -- finding, previewing and organising media.
- GRADE: the grading screen -- Record, the Timeline's thumbnail strip,
  the Stack, Parameters, Scopes. Also what Reset to Default Workspace
  and a first launch give you.

Applied from Workspace > Suggested Layouts (Alt+1, Alt+2) or the buttons
in the menu bar's top-right corner. A mixin: MainWindow inherits it, so
these methods share the window's own `self`.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy

from .constants import MEDIA_LAYOUT, PANEL_TOGGLES, SUGGESTED_LAYOUT_PANELS, SUGGESTED_LAYOUTS


class SuggestedLayoutMixin:
    def _build_suggested_layouts_ui(self, workspace_menu):
        """A "Suggested Layouts" group in the Workspace menu (with the
        Alt+N shortcuts) and plain buttons in the menu bar's corner."""
        corner = QFrame()
        corner.setObjectName("suggestedLayouts")
        corner.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 2, 6, 2)
        corner_layout.setSpacing(4)
        workspace_menu.addSeparator()
        submenu = workspace_menu.addMenu("Suggested Layouts")
        for number, (layout_key, label) in enumerate(SUGGESTED_LAYOUTS, start=1):
            action = submenu.addAction(label)
            action.setShortcut(QKeySequence(f"Alt+{number}"))
            action.triggered.connect(lambda _checked=False, k=layout_key: self.apply_suggested_layout(k))
            button = QPushButton(label)
            button.setObjectName("suggestedLayoutButton")
            button.setToolTip(f"Arrange the panels for {label.lower()} work (Alt+{number})")
            button.clicked.connect(lambda _checked=False, k=layout_key: self.apply_suggested_layout(k))
            corner_layout.addWidget(button)
        # Keep a Python reference: PySide doesn't hand ownership of a corner
        # widget to the menu bar, so a local-only one is deleted on return.
        self._suggested_layouts_corner = corner
        self.menuBar().setCornerWidget(corner, Qt.Corner.TopRightCorner)

    def apply_suggested_layout(self, layout_key):
        """Show `layout_key`'s panels (hiding the rest), docked in their
        suggested spots. Floating panels are docked first (see
        _dock_all_floating_panels)."""
        self._dock_all_floating_panels()
        self._show_only_panels_of(layout_key)  # before: proportions count shown docks
        if layout_key == MEDIA_LAYOUT:
            self._build_media_layout()
        else:
            self._build_default_dock_layout()
        # ...and after: placing a dock can show it (e.g. Source, see
        # _place_source_viewer_beside_record).
        self._show_only_panels_of(layout_key)
        self._snapshot_all_docked_state()

    def _show_only_panels_of(self, layout_key):
        for _action_attr, _label, frame_attr, _default_visible in PANEL_TOGGLES:
            getattr(self, frame_attr).setVisible(frame_attr in SUGGESTED_LAYOUT_PANELS[layout_key])

    def _build_media_layout(self):
        """Browser top-left, Source viewer top-centre, Media Libraries
        across the whole bottom (the bottom dock area owns both bottom
        corners, so it spans the full width)."""
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.browser_frame)
        self.splitDockWidget(self.browser_frame, self.source_viewer_frame, Qt.Orientation.Horizontal)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.image_library_frame)
        width, height = max(1, self.width()), max(1, self.height())
        self.resizeDocks([self.browser_frame, self.source_viewer_frame],
                         [round(width * 0.24), round(width * 0.76)], Qt.Orientation.Horizontal)
        self.resizeDocks([self.source_viewer_frame, self.image_library_frame],
                         [round(height * 0.55), round(height * 0.45)], Qt.Orientation.Vertical)
