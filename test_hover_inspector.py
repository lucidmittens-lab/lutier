"""Tests for hover_inspector.py -- run directly: python3 test_hover_inspector.py

Full native Alt+hover can't be meaningfully simulated headlessly (no real
OS-level modifier-key/cursor-position state to drive
QApplication.keyboardModifiers()/widgetAt() with) -- these tests exercise
the decomposed, testable pieces directly instead: the registry/ancestor-
walk logic HoverInspector._refresh() relies on, and stack_panel.py's
_stage_info_text (the actual per-stage-kind content, independent of
whether/how it ends up on screen).

Requires a real (if offscreen) QApplication for the widget-hierarchy
tests -- same headless-Qt setup as every other GUI-touching verification
in this project (see RUNNING.md).
"""
import sys

from PySide6.QtWidgets import QApplication, QListWidget, QWidget

import hover_inspector
import stack as st
from stack_panel import _stage_info_text


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


# ---- HoverInspector registry / ancestor walk ------------------------------

def test_registered_widget_is_found_directly():
    _app()
    lst = QListWidget()
    inspector = hover_inspector.HoverInspector.instance()
    inspector.register(lst, lambda item: "info")
    try:
        found_widget, found_callback = inspector._registered_ancestor(lst)
        assert found_widget is lst
        assert found_callback("anything") == "info"
    finally:
        inspector.unregister(lst)
    print("HoverInspector: a directly-registered widget is found by "
          "_registered_ancestor -- OK")


def test_registered_widget_is_found_via_a_child_widget():
    """The real case this exists for: QApplication.widgetAt() returns a
    QListWidget's VIEWPORT (a child widget), not the QListWidget itself
    -- _registered_ancestor must walk up parentWidget() to find the
    actually-registered ancestor."""
    _app()
    lst = QListWidget()
    child = QWidget(lst)  # simulates the viewport-is-a-child-of-the-list relationship
    inspector = hover_inspector.HoverInspector.instance()
    inspector.register(lst, lambda item: "info")
    try:
        found_widget, _ = inspector._registered_ancestor(child)
        assert found_widget is lst
    finally:
        inspector.unregister(lst)
    print("HoverInspector: a registered ancestor is found starting from "
          "one of its OWN child widgets (e.g. a list's viewport) -- OK")


def test_unregistered_widget_is_not_found():
    _app()
    lst = QListWidget()
    inspector = hover_inspector.HoverInspector.instance()
    found_widget, found_callback = inspector._registered_ancestor(lst)
    assert found_widget is None
    assert found_callback is None
    print("HoverInspector: a never-registered widget (and no registered "
          "ancestor) returns (None, None) -- OK")


def test_unregister_actually_removes_it():
    _app()
    lst = QListWidget()
    inspector = hover_inspector.HoverInspector.instance()
    inspector.register(lst, lambda item: "info")
    inspector.unregister(lst)
    found_widget, _ = inspector._registered_ancestor(lst)
    assert found_widget is None
    print("HoverInspector.unregister: a widget is no longer found after "
          "unregistering it -- OK")


# ---- stack_panel._stage_info_text -----------------------------------------

def test_stage_info_text_includes_label_kind_and_disabled_state():
    stage = st.make_colorspace_stage("sRGB", "ACEScg", label="My Stage")
    stage.enabled = False
    text = _stage_info_text(stage)
    assert "My Stage" in text
    assert "[colorspace]" in text
    assert "Disabled" in text
    print("_stage_info_text: includes label, kind, and disabled state -- OK")


def test_stage_info_text_shows_non_default_opacity():
    stage = st.make_printer_lights_stage(r_points=5, opacity=0.5)
    text = _stage_info_text(stage)
    assert "50%" in text
    print("_stage_info_text: shows a non-default opacity -- OK")


def test_stage_info_text_does_not_show_default_opacity():
    stage = st.make_printer_lights_stage(r_points=5)
    text = _stage_info_text(stage)
    assert "Opacity" not in text
    print("_stage_info_text: omits opacity entirely at the default "
          "(100%) -- OK")


def test_stage_info_text_for_lut_stage_shows_source_path():
    lut = st.colour.LUT3D(size=3)
    stage = st.make_lut_stage(lut)
    stage.params["source_kind"] = "file"
    stage.params["source_path"] = "/some/real/path.cube"
    text = _stage_info_text(stage)
    assert "/some/real/path.cube" in text
    print("_stage_info_text: a file-sourced 'lut' stage shows its source "
          "path -- OK")


def test_stage_info_text_for_cdl_stage_shows_sop_sat_and_style():
    stage = st.make_cdl_stage(slope=(1.1, 1.0, 0.9), sat=1.2, style="no_clip")
    text = _stage_info_text(stage)
    assert "1.100" in text
    assert "1.200" in text
    assert "no_clip" in text
    print("_stage_info_text: a 'cdl' stage shows slope/sat/style -- OK")


def test_stage_info_text_for_aces_drt_stage_shows_the_transform_chain():
    stage = st.make_aces_drt_stage("ACEScg", "sRGB", "ACES 1.0 SDR-video")
    text = _stage_info_text(stage)
    assert "ACEScg" in text
    assert "sRGB" in text
    assert "ACES 1.0 SDR-video" in text
    print("_stage_info_text: an 'aces_drt' stage shows working space -> "
          "display/view -- OK")


def test_stage_info_text_for_fork_merge_shows_blend():
    stage = st.make_fork_merge_stage(blend=0.75)
    text = _stage_info_text(stage)
    assert "75%" in text
    print("_stage_info_text: a 'fork_merge' stage shows its blend -- OK")


def test_stage_info_text_shows_branch_tag():
    stage = st.make_colorspace_stage("sRGB", "ACEScg")
    stage.branch = "a"
    text = _stage_info_text(stage)
    assert "Branch: A" in text
    print("_stage_info_text: a branch-tagged stage shows which branch -- OK")


if __name__ == "__main__":
    test_registered_widget_is_found_directly()
    test_registered_widget_is_found_via_a_child_widget()
    test_unregistered_widget_is_not_found()
    test_unregister_actually_removes_it()
    test_stage_info_text_includes_label_kind_and_disabled_state()
    test_stage_info_text_shows_non_default_opacity()
    test_stage_info_text_does_not_show_default_opacity()
    test_stage_info_text_for_lut_stage_shows_source_path()
    test_stage_info_text_for_cdl_stage_shows_sop_sat_and_style()
    test_stage_info_text_for_aces_drt_stage_shows_the_transform_chain()
    test_stage_info_text_for_fork_merge_shows_blend()
    test_stage_info_text_shows_branch_tag()
    print()
    print("All hover_inspector tests passed.")
