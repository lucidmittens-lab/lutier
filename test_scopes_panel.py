"""Tests for scopes_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 test_scopes_panel.py

Previously untested (no test file at all -- found in code review, same
"NOT TESTED IN THIS ENVIRONMENT" gap this module's own docstring used to
document, from back when this environment couldn't construct real Qt
widgets headless). Focused on set_image_array/showEvent's own visibility-
skip fix specifically (a real, previously-shipped performance bug: every
after_image_changed tick -- every slider drag, every video-playback frame
-- triggered a full histogram/waveform/vectorscope recompute even while
the Scopes panel wasn't visible at all), not a full survey of every scope
kind's own rendering math.
"""
import sys

import numpy as np
from PySide6.QtWidgets import QApplication, QMainWindow

from scopes_panel import ScopesPanel, SCOPE_HISTOGRAM


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _test_image():
    rng = np.random.default_rng(0)
    return rng.uniform(0.0, 1.0, size=(8, 12, 3)).astype(np.float32)


def _shown_panel():
    """A ScopesPanel inside a real, shown QMainWindow so isVisible()
    reads real values -- same pattern test_stack_panel.py's own
    _shown_panel_with_fork uses. Returns (panel, win)."""
    _app()
    panel = ScopesPanel()
    win = QMainWindow()
    win.setCentralWidget(panel)
    win.resize(400, 300)
    win.show()
    _app().processEvents()
    assert panel.isVisible(), "test setup: the panel must actually be visible here"
    return panel, win


def test_set_image_array_renders_immediately_while_visible():
    panel, _win = _shown_panel()
    render_calls = []
    original = panel._render_current_scope
    panel._render_current_scope = lambda: (render_calls.append(1), original())[1]

    panel.set_image_array(_test_image())

    assert render_calls == [1]
    assert panel._stale_render_pending is False
    print("ScopesPanel.set_image_array: renders immediately while the "
          "panel is visible -- OK")


def test_set_image_array_skips_render_while_hidden():
    """Regression guard for a real, previously-shipped performance bug
    (found in code review): after_image_changed fires on EVERY stack-
    edit tick and every video-playback frame -- a full histogram/
    waveform/vectorscope/volumetric recompute on each one, even while
    the Scopes panel isn't visible at all (hidden via the Workspace
    menu, tabbed away, floated elsewhere), was pure wasted work."""
    panel, win = _shown_panel()
    win.hide()
    _app().processEvents()
    assert not panel.isVisible(), "test setup: the panel must actually be hidden here"

    render_calls = []
    original = panel._render_current_scope
    panel._render_current_scope = lambda: (render_calls.append(1), original())[1]

    panel.set_image_array(_test_image())

    assert render_calls == [], "must not render while hidden"
    assert panel._stale_render_pending is True
    # The array itself is still kept current, even though the render was skipped.
    assert panel._image_array is not None
    print("ScopesPanel.set_image_array: skips the actual render while "
          "the panel is hidden, still keeps the array current -- OK")


def test_show_event_runs_the_deferred_render_when_stale():
    panel, win = _shown_panel()
    win.hide()
    _app().processEvents()
    panel.set_image_array(_test_image())
    assert panel._stale_render_pending is True

    render_calls = []
    original = panel._render_current_scope
    panel._render_current_scope = lambda: (render_calls.append(1), original())[1]

    win.show()
    _app().processEvents()

    assert render_calls == [1], "becoming visible again must run exactly one deferred render"
    assert panel._stale_render_pending is False
    print("ScopesPanel.showEvent: runs the deferred render exactly once "
          "when the panel becomes visible again after a skipped update -- OK")


def test_show_event_is_a_noop_when_nothing_was_pending():
    """Becoming visible when set_image_array was never skipped (e.g. a
    panel that was visible the whole time, or has never received an
    image at all) must not trigger a redundant/spurious render."""
    panel, win = _shown_panel()
    render_calls = []
    original = panel._render_current_scope
    panel._render_current_scope = lambda: (render_calls.append(1), original())[1]

    win.hide()
    _app().processEvents()
    win.show()
    _app().processEvents()

    assert render_calls == [], "showEvent must be a no-op when nothing was left pending"
    print("ScopesPanel.showEvent: a no-op when no render was pending -- OK")


def test_scope_switch_still_renders_immediately_regardless_of_the_skip_flag():
    """Explicitly picking a different scope (Histogram/Waveform/
    Vectorscope/Volumetric) is a direct user action, not a background
    tick -- must always render immediately when the panel is visible,
    unaffected by the set_image_array visibility-skip mechanism."""
    panel, _win = _shown_panel()
    panel.set_image_array(_test_image())

    render_calls = []
    original = panel._render_current_scope
    panel._render_current_scope = lambda: (render_calls.append(1), original())[1]
    panel._on_scope_changed(SCOPE_HISTOGRAM)

    assert render_calls == [1]
    print("ScopesPanel._on_scope_changed: always renders immediately "
          "while visible -- OK")


if __name__ == "__main__":
    test_set_image_array_renders_immediately_while_visible()
    test_set_image_array_skips_render_while_hidden()
    test_show_event_runs_the_deferred_render_when_stale()
    test_show_event_is_a_noop_when_nothing_was_pending()
    test_scope_switch_still_renders_immediately_regardless_of_the_skip_flag()
    print()
    print("All scopes_panel tests passed.")
