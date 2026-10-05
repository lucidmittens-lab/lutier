"""run directly: QT_QPA_PLATFORM=offscreen python3 test_source_viewer.py"""

import os
import tempfile
from PySide6.QtWidgets import QApplication
from source_viewer import SourceViewerPanel
from media_library import MediaEntry
from test_frame_server import _write_video

_TMP = tempfile.TemporaryDirectory()

def _video(name, n=48):
    path = os.path.join(_TMP.name, name + ".mkv")
    _write_video(path, n)
    return path

def _panel():
    _app()
    return SourceViewerPanel()

def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

def test_load_entry_shows_the_clip():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    assert panel.player.frame_count == 48
    assert panel.player.frame_index == 0
    assert panel.name_label.text() == "Shot"
    assert panel.timecode_edit.text() == "01:00:00:00"
    assert panel.insert_button.isEnabled()
    print("test_load_entry_shows_the_clip -- OK")

def test_seek_and_step():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    panel.seek(10)
    assert panel.player.frame_index == 10
    assert panel.scrub.value() == 10
    assert panel.timecode_edit.text() == "01:00:00:10"
    panel.step(5)
    assert panel.player.frame_index == 15
    panel.step(-100)
    assert panel.player.frame_index == 0
    panel.seek(1000)
    assert panel.player.frame_index == 47
    print("test_seek_and_step -- OK")

def test_marks_and_marked_range():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    assert panel.marked_range() == (0, 47)
    panel.seek(5)
    panel.mark_in()
    panel.seek(20)
    panel.mark_out()
    assert panel.marked_range() == (5, 20)
    assert "DUR" in panel.marks_label.text()
    panel.clear_marks()
    assert panel.marked_range() == (0, 47)
    panel.seek(10)
    panel.mark_in()
    panel.seek(3)
    panel.mark_out()
    assert panel.in_point is None
    assert panel.out_point == 3
    print("test_marks_and_marked_range -- OK")

def test_edit_requested_carries_the_marked_range():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    edits = []
    panel.edit_requested.connect(lambda mode, spec: edits.append((mode, spec)))
    panel.seek(4)
    panel.mark_in()
    panel.seek(9)
    panel.mark_out()
    panel.request_edit("insert")
    panel.request_edit("append")
    assert len(edits) == 2
    assert edits[0][0] == "insert"
    assert edits[0][1]["in_frame"] == 4
    assert edits[0][1]["out_frame"] == 9
    assert edits[0][1]["path"] == path
    assert edits[0][1]["kind"] == "video"
    assert edits[0][1]["name"] == "Shot"
    assert edits[1][0] == "append"
    print("test_edit_requested_carries_the_marked_range -- OK")

def test_typed_timecode_seeks():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    panel.timecode_edit.setText("01:00:00:12")
    panel._on_timecode_entered()
    assert panel.player.frame_index == 12
    panel.timecode_edit.setText("+6")
    panel._on_timecode_entered()
    assert panel.player.frame_index == 18
    panel.timecode_edit.setText("garbage")
    panel._on_timecode_entered()
    assert panel.player.frame_index == 18
    assert panel.timecode_edit.text() == "01:00:00:18"
    print("test_typed_timecode_seeks -- OK")

def test_k_held_with_l_steps_one_frame():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    panel.seek(10)
    panel._k_held = True
    panel._shuttle_key("l")
    assert panel.player.frame_index == 11
    panel._shuttle_key("j")
    assert panel.player.frame_index == 10
    assert not panel.player.is_playing
    print("test_k_held_with_l_steps_one_frame -- OK")

def test_l_plays_and_k_stops():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    panel._shuttle_key("l")
    assert panel.player.is_playing
    assert panel.player.speed == 1
    panel._shuttle_key("l")
    assert panel.player.speed == 2
    panel._shuttle_key("k")
    assert not panel.player.is_playing
    print("test_l_plays_and_k_stops -- OK")

def test_playback_advances_frames():
    from PySide6.QtCore import QEventLoop, QTimer
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    panel._shuttle_key("l")
    # A real event loop, not QTest.qWait: qWait keeps the GIL while it
    # spins, starving the FrameServer's decode thread (the app's own
    # event loop releases it).
    loop = QEventLoop()
    QTimer.singleShot(400, loop.quit)
    loop.exec()
    panel._shuttle_key("k")
    assert panel.player.frame_index > 0
    print("test_playback_advances_frames -- OK")

def test_missing_file_shows_in_place_not_a_dialog():
    panel = _panel()
    assert not panel.load_path("/nonexistent.mov", "video", "Gone")
    assert panel.player.path is None
    print("test_missing_file_shows_in_place_not_a_dialog -- OK")

def test_clear_resets():
    panel = _panel()
    path = _video("test_clip")
    entry = MediaEntry("Shot", path, media_kind="video", frame_count=48, frame_rate=24.0, metadata={"start_timecode": "01:00:00:00"})
    panel.load_entry(entry)
    panel.clear()
    assert panel.player.path is None
    assert not panel.insert_button.isEnabled()
    print("test_clear_resets -- OK")

if __name__ == "__main__":
    test_load_entry_shows_the_clip()
    test_seek_and_step()
    test_marks_and_marked_range()
    test_edit_requested_carries_the_marked_range()
    test_typed_timecode_seeks()
    test_k_held_with_l_steps_one_frame()
    test_l_plays_and_k_stops()
    test_playback_advances_frames()
    test_missing_file_shows_in_place_not_a_dialog()
    test_clear_resets()
    print()
    print("All source_viewer tests passed.")
