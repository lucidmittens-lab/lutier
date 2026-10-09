"""main_window_parts/constants.py -- MainWindow's panel table (PANEL_TOGGLES:
each panel's toggle action, menu label, frame attribute and default
visibility) and the default workspace state.
"""


# (attribute name to store the QAction under, button label, target DOCK
# attribute name, default visible on startup). "_frame" is now a
# QDockWidget instance (see _make_dock), not a PanelFrame -- the name is
# kept from before the docking migration purely to avoid renaming every
# external reference (tests, GROUP-splitter-era code) for no functional
# reason; PanelFrame is still used, just as the dock's inner border/
# background only (see _make_dock/_build_dock_title_bar).
#
# All nine toggles live in the Workspace menu (see this module's
# docstring) -- LEFT/CENTER/RIGHT here just group them roughly matching
# each panel's default column (Browser+Media Libraries+LUT
# Library+Galleries=left column, Viewer+Stack=center column, Stack
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
# migration, still matches docs/USER_MANUAL.md/help_text.py's shortcut table.
# IDENTITY PASS (2026-10-09): the default workspace is the grading screen
# from the chosen mockup -- Record, the Timeline's thumbnail strip, Stack,
# Parameters, Scopes. Every other
# panel starts hidden (one click away in the Workspace menu / Shift+N) and
# keeps its default slot for when it's shown.
PANEL_TOGGLES_LEFT = [
    ("browser_toggle", "Browser", "browser_frame", False),
    ("image_library_toggle", "Media Libraries", "image_library_frame", False),
    ("lut_library_toggle", "LUT Library", "lut_library_frame", False),
    ("stack_memories_toggle", "Galleries", "stack_memories_frame", False),
]
PANEL_TOGGLES_CENTER = [
    ("sample_image_toggle", "Record", "sample_image_frame", True),
    ("stack_toggle", "Stack", "stack_frame", True),
]
PANEL_TOGGLES_RIGHT = [
    ("stack_params_toggle", "Parameters", "stack_params_frame", True),
    ("scopes_toggle", "Scopes", "scopes_frame", True),
    ("viewer_3d_toggle", "Graph", "viewer_3d_frame", False),
]
# Timeline is the newest panel (Continuity Grading Timeline) -- appended
# here rather than folded into PANEL_TOGGLES_LEFT's own library column so
# it lands on the already-documented Shift+0 fallback (position 10) above
# instead of bumping every existing Shift+1..9 assignment down by one.
PANEL_TOGGLES_TIMELINE = [
    # Shown by default since the Thumbnails view (2026-10-09): the
    # grading screen's clip navigator, a slim band under the viewer.
    ("timeline_toggle", "Timeline", "timeline_frame", True),
    # Source viewer (Tom, 2026-10-05: "we must have both source and record
    # windows") -- appended last so every existing Shift+N panel toggle
    # keeps its number.
    ("source_viewer_toggle", "Source", "source_viewer_frame", False),
    # The selected media clip's details (metadata_panel.py) -- part of
    # the Media suggested layout.
    ("metadata_toggle", "Metadata", "metadata_frame", False),
    # The Render suggested layout's two panels (render_settings_panel.py,
    # render_queue_panel.py).
    ("render_settings_toggle", "Render Settings", "render_settings_frame", False),
    ("render_queue_toggle", "Render Queue", "render_queue_frame", False),
]
PANEL_TOGGLES = (
    PANEL_TOGGLES_LEFT + PANEL_TOGGLES_CENTER + PANEL_TOGGLES_RIGHT + PANEL_TOGGLES_TIMELINE)


# Only window_size now -- panel visibility/position/floating all live in
# the dock layout itself (see _build_default_dock_layout for the DEFAULT
# arrangement, get_workspace_state/load_workspace_state for how an
# arbitrary drag-built arrangement is captured/restored via QMainWindow's
# own saveState()/restoreState()). Before the docking migration this dict
# also carried "panels"/"splitters"/"floating" keys mirroring
# get_workspace_state()'s old hand-rolled shape -- gone now that shape
# itself is gone (see this module's docstring, WORKSPACE PERSISTENCE).
DEFAULT_WORKSPACE_STATE = {
    # The un-maximized size -- the app itself launches MAXIMIZED unless
    # a remembered workspace says otherwise (per Tom, 2026-10-05: "full
    # screen by default with a comfy amount of space per panel").
    "window_size": [1200, 800],
}


# Suggested layouts (main_window_parts/suggested_layouts.py): one-click
# panel arrangements for a kind of work -- not modes. Order = menu/button
# order (and Alt+1, Alt+2, Alt+3).
MEDIA_LAYOUT = "media"
GRADE_LAYOUT = "grade"
RENDER_LAYOUT = "render"
SUGGESTED_LAYOUTS = ((MEDIA_LAYOUT, "Media"), (GRADE_LAYOUT, "Grade"), (RENDER_LAYOUT, "Render"))
SUGGESTED_LAYOUT_PANELS = {
    MEDIA_LAYOUT: {"browser_frame", "source_viewer_frame", "metadata_frame", "image_library_frame"},
    GRADE_LAYOUT: {frame_attr for _a, _l, frame_attr, default_visible in PANEL_TOGGLES
                   if default_visible},
    RENDER_LAYOUT: {"render_settings_frame", "sample_image_frame", "render_queue_frame",
                    "timeline_frame"},
}
