"""Tests for browser_panel.py -- run directly:
QT_QPA_PLATFORM=offscreen python3 -m tests.test_browser_panel
"""
import sys
from pathlib import Path
import tempfile

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import browser_panel as bp
from browser_panel import BrowserPanel, KIND_LUT, KIND_CDL, KIND_STILL, KIND_VIDEO, SEARCH_DEBOUNCE_MS, LUT_PATH_MIME, COLUMN_NAME

def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app

def test_kind_for_path_classifies_correctly():
    _app()
    assert bp.kind_for_path(Path("shot.mov")) == KIND_VIDEO
    assert bp.kind_for_path(Path("plate.exr")) == KIND_STILL
    assert bp.kind_for_path(Path("look.cube")) == KIND_LUT
    assert bp.kind_for_path(Path("grade.cdl")) == KIND_CDL
    assert bp.kind_for_path(Path("notes.txt")) is None
    assert bp.kind_for_path(Path("SHOT.MOV")) == KIND_VIDEO
    print("kind_for_path classifies correctly and is case-insensitive -- OK")

def test_scan_folder_non_recursive():
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        results, truncated = bp.scan_folder(temp_dir, recursive=False)
        assert [str(path) for path, _ in results] == [
            str(Path(temp_dir) / "grade.cdl"),
            str(Path(temp_dir) / "look.cube"),
            str(Path(temp_dir) / "plate.exr"),
            str(Path(temp_dir) / "shot.mov")
        ]
        assert not truncated
        print("scan_folder non-recursive lists only top-level recognized files -- OK")

def test_scan_folder_recursive():
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        results, truncated = bp.scan_folder(temp_dir, recursive=True)
        # Sorted by file name (not path), hidden .cache/ skipped.
        assert [str(path) for path, _ in results] == [
            str(Path(temp_dir) / "sub" / "deep.cube"),
            str(Path(temp_dir) / "sub" / "deep.mxf"),
            str(Path(temp_dir) / "grade.cdl"),
            str(Path(temp_dir) / "look.cube"),
            str(Path(temp_dir) / "plate.exr"),
            str(Path(temp_dir) / "shot.mov"),
        ]
        assert not truncated
        print("scan_folder recursive lists all recognized files including subfolders -- OK")

def test_scan_folder_kinds_and_name_filter():
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        results, truncated = bp.scan_folder(temp_dir, recursive=True, kinds=("still", "video"))
        assert [str(path) for path, _ in results] == [
            str(Path(temp_dir) / "sub" / "deep.mxf"),
            str(Path(temp_dir) / "plate.exr"),
            str(Path(temp_dir) / "shot.mov"),
        ]
        assert not truncated

        results, truncated = bp.scan_folder(temp_dir, recursive=True, name_filter="*.cube")
        assert [str(path) for path, _ in results] == [
            str(Path(temp_dir) / "sub" / "deep.cube"),
            str(Path(temp_dir) / "look.cube"),
        ]
        assert not truncated

        results, truncated = bp.scan_folder(temp_dir, name_filter="DEEP", recursive=True)
        assert [str(path) for path, _ in results] == [
            str(Path(temp_dir) / "sub" / "deep.cube"),
            str(Path(temp_dir) / "sub" / "deep.mxf")
        ]
        assert not truncated
        print("scan_folder with kinds and name_filter works -- OK")

def test_scan_folder_limit():
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        results, truncated = bp.scan_folder(temp_dir, limit=2)
        assert len(results) == 2
        assert truncated
        print("scan_folder with limit=2 returns 2 results and truncated True -- OK")

def test_browser_panel_set_folder_and_recursive():
    _app()
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        panel = BrowserPanel()
        panel.set_folder(temp_dir)
        assert sorted(Path(p).name for p in panel.listed_paths()) == [
            "grade.cdl", "look.cube", "plate.exr", "shot.mov"
        ]

        panel.recursive_checkbox.setChecked(True)
        panel.refresh()
        assert sorted(Path(p).name for p in panel.listed_paths()) == [
            "deep.cube", "deep.mxf", "grade.cdl", "look.cube", "plate.exr", "shot.mov"
        ]

        panel.type_combo.setCurrentIndex(1)
        panel.refresh()
        assert sorted(Path(p).name for p in panel.listed_paths()) == [
            "deep.mxf", "plate.exr", "shot.mov"
        ]
        print("BrowserPanel: set_folder, recursive_checkbox, and type_combo work -- OK")

def test_search_box_debounce():
    _app()
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        panel = BrowserPanel()
        panel.set_folder(temp_dir)
        panel.search_box.setText("look")
        assert panel._search_debounce_timer.isActive()
        assert sorted(Path(p).name for p in panel.listed_paths()) == [
            "grade.cdl", "look.cube", "plate.exr", "shot.mov"
        ]

        QTest.qWait(SEARCH_DEBOUNCE_MS + 100)
        assert sorted(Path(p).name for p in panel.listed_paths()) == [
            "look.cube"
        ]
        print("Search box debounce works -- OK")

def test_drag_payload():
    _app()
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        panel = BrowserPanel()
        panel.set_folder(temp_dir)
        panel.recursive_checkbox.setChecked(True)
        panel.refresh()

        look_item = None
        for i in range(panel.file_list.topLevelItemCount()):
            item = panel.file_list.topLevelItem(i)
            if item.text(COLUMN_NAME) == "look.cube":
                look_item = item
                break
        assert look_item is not None

        mime = panel.file_list.mimeData([look_item])
        assert mime.hasUrls()
        assert mime.urls()[0].toLocalFile().endswith("look.cube")
        assert mime.hasFormat(LUT_PATH_MIME)

        media_items = [panel.file_list.topLevelItem(i) for i in range(panel.file_list.topLevelItemCount()) if panel.file_list.topLevelItem(i).text(COLUMN_NAME) in ("shot.mov", "plate.exr")]
        mime = panel.file_list.mimeData(media_items)
        assert mime.hasUrls()
        assert len(mime.urls()) == 2
        assert not mime.hasFormat(LUT_PATH_MIME)
        print("Drag payload works -- OK")

def test_double_click_routing():
    _app()
    with tempfile.TemporaryDirectory() as temp_dir:
        (Path(temp_dir) / "shot.mov").touch()
        (Path(temp_dir) / "plate.exr").touch()
        (Path(temp_dir) / "look.cube").touch()
        (Path(temp_dir) / "grade.cdl").touch()
        (Path(temp_dir) / "notes.txt").touch()
        (Path(temp_dir) / "sub").mkdir()
        (Path(temp_dir) / "sub" / "deep.mxf").touch()
        (Path(temp_dir) / "sub" / "deep.cube").touch()
        (Path(temp_dir) / ".cache").mkdir()
        (Path(temp_dir) / ".cache" / "hidden.mov").touch()

        panel = BrowserPanel()
        panel.set_folder(temp_dir)
        panel.recursive_checkbox.setChecked(True)
        panel.refresh()

        emitted = {"lut": [], "cdl": [], "media": []}
        panel.file_selected.connect(emitted["lut"].append)
        panel.cdl_file_selected.connect(emitted["cdl"].append)
        panel.media_files_activated.connect(emitted["media"].append)

        def double_click(name):
            for values in emitted.values():
                values.clear()
            item = next(panel.file_list.topLevelItem(i)
                        for i in range(panel.file_list.topLevelItemCount())
                        if panel.file_list.topLevelItem(i).text(COLUMN_NAME) == name)
            panel._on_item_double_clicked(item)
            return {k: v[:] for k, v in emitted.items()}

        assert double_click("look.cube") == {"lut": [str(Path(temp_dir) / "look.cube")], "cdl": [], "media": []}
        assert double_click("grade.cdl") == {"lut": [], "cdl": [str(Path(temp_dir) / "grade.cdl")], "media": []}
        assert double_click("shot.mov") == {"lut": [], "cdl": [], "media": [[str(Path(temp_dir) / "shot.mov")]]}
    print()
    print("All browser_panel tests passed.")
