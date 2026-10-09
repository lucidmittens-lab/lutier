"""
test_transport_logic.py -- run directly: python3 -m tests.test_transport_logic -- Qt-free
"""
from transport_logic import Shuttle, parse_timecode_entry
import timeline as tl

def test_shuttle_press_l():
    shuttle = Shuttle()
    assert shuttle.press_l() == ("speed", 1)
    assert shuttle.press_l() == ("speed", 2)
    assert shuttle.press_l() == ("speed", 4)
    assert shuttle.press_l() == ("speed", 8)
    assert shuttle.press_l() == ("speed", 8)
    print("shuttle_press_l -- OK")

def test_shuttle_press_j():
    shuttle = Shuttle()
    shuttle.press_l()
    assert shuttle.press_j() == ("speed", -1)
    assert shuttle.press_j() == ("speed", -2)
    print("shuttle_press_j -- OK")

def test_shuttle_press_k():
    shuttle = Shuttle()
    shuttle.press_l()
    shuttle.press_k()
    assert shuttle.press_k() == ("speed", 0)
    assert shuttle.press_l() == ("speed", 1)
    print("shuttle_press_k -- OK")

def test_shuttle_press_l_k_held():
    shuttle = Shuttle()
    assert shuttle.press_l(k_held=True) == ("step", 1)
    assert shuttle.press_j(k_held=True) == ("step", -1)
    assert shuttle.speed == 0
    print("shuttle_press_l_k_held -- OK")

def test_parse_timecode_entry_full_tc():
    assert parse_timecode_entry("00:00:01:00", 0, 24) == 24
    assert parse_timecode_entry("01:00:00:00", 0, 24) == 86400
    print("parse_timecode_entry_full_tc -- OK")

def test_parse_timecode_entry_partial_overlay():
    assert parse_timecode_entry("00:42:46:00", 10, 24, 61579) == 5
    assert parse_timecode_entry("1000", 24*60*5, 24) == 7440
    assert parse_timecode_entry("215", 24*60*5, 24) == 7263
    print("parse_timecode_entry_partial_overlay -- OK")

def test_parse_timecode_entry_relative():
    assert parse_timecode_entry("+200", 100, 24) == 148
    assert parse_timecode_entry("-12", 100, 24) == 88
    assert parse_timecode_entry("+1:00", 100, 24) == 124
    print("parse_timecode_entry_relative -- OK")

def test_parse_timecode_entry_separators():
    assert parse_timecode_entry("00;00;01;00", 0, 24) == 24
    assert parse_timecode_entry("00.00.01.00", 0, 24) == 24
    print("parse_timecode_entry_separators -- OK")

def test_parse_timecode_entry_garbage():
    assert parse_timecode_entry("", 0, 24) is None
    assert parse_timecode_entry("abc", 0, 24) is None
    assert parse_timecode_entry("1:x:3", 0, 24) is None
    assert parse_timecode_entry("100", 0, 0) is None
    print("parse_timecode_entry_garbage -- OK")

def _clip(name, n):
    return tl.TimelineClip(name, f"/{name}.mov", "video", 0, n - 1)

def _layout(t):
    return [(c.name, c.frame_count) for c in t.clips]

def test_timeline_insert_clip_at_record_frame():
    t = tl.Timeline()
    t.clips = [_clip("A", 10), _clip("B", 10)]
    index = t.insert_clip_at_record_frame(_clip("X", 4), 5)
    assert _layout(t) == [("A", 5), ("X", 4), ("A", 5), ("B", 10)]
    assert index == 1
    assert t.total_frame_count() == 24
    print("timeline_insert_clip_at_record_frame -- OK")

def test_timeline_insert_clip_at_record_frame_edges():
    t = tl.Timeline(clips=[_clip("A", 10), _clip("B", 10)])
    assert t.insert_clip_at_record_frame(_clip("X", 4), 0) == 0
    assert _layout(t)[0] == ("X", 4)
    t = tl.Timeline(clips=[_clip("A", 10), _clip("B", 10)])
    assert t.insert_clip_at_record_frame(_clip("Y", 4), 20) == 2
    assert _layout(t) == [("A", 10), ("B", 10), ("Y", 4)]
    print("timeline_insert_clip_at_record_frame_edges -- OK")

def test_timeline_overwrite_clip_at_record_frame():
    t = tl.Timeline()
    t.clips = [_clip("A", 10), _clip("B", 10)]
    t.overwrite_clip_at_record_frame(_clip("X", 8), 6)
    assert _layout(t) == [("A", 6), ("X", 8), ("B", 6)]
    print("timeline_overwrite_clip_at_record_frame -- OK")

def test_timeline_overwrite_clip_at_record_frame_exact():
    t = tl.Timeline()
    t.clips = [_clip("A", 10), _clip("B", 10)]
    t.overwrite_clip_at_record_frame(_clip("X", 10), 10)
    assert _layout(t) == [("A", 10), ("X", 10)]
    print("timeline_overwrite_clip_at_record_frame_exact -- OK")

def test_timeline_overwrite_clip_at_record_frame_past_end():
    t = tl.Timeline()
    t.clips = [_clip("A", 10), _clip("B", 10)]
    t.overwrite_clip_at_record_frame(_clip("X", 30), 15)
    assert _layout(t) == [("A", 10), ("B", 5), ("X", 30)]
    print("timeline_overwrite_clip_at_record_frame_past_end -- OK")

def test_timeline_append_clip():
    t = tl.Timeline()
    t.clips = [_clip("A", 10), _clip("B", 10)]
    index = t.append_clip(_clip("X", 4))
    assert _layout(t) == [("A", 10), ("B", 10), ("X", 4)]
    assert index == 2
    print("timeline_append_clip -- OK")

def test_timeline_record_start_of():
    a, b = _clip("A", 10), _clip("B", 10)
    t = tl.Timeline(clips=[a, b])
    assert t.record_start_of(a.id) == 0
    assert t.record_start_of(b.id) == 10
    try:
        t.record_start_of(-1)
    except KeyError:
        pass
    else:
        raise AssertionError("KeyError not raised for unknown id")
    print("timeline_record_start_of -- OK")

if __name__ == "__main__":
    test_shuttle_press_l()
    test_shuttle_press_j()
    test_shuttle_press_k()
    test_shuttle_press_l_k_held()
    test_parse_timecode_entry_full_tc()
    test_parse_timecode_entry_partial_overlay()
    test_parse_timecode_entry_relative()
    test_parse_timecode_entry_separators()
    test_parse_timecode_entry_garbage()
    test_timeline_insert_clip_at_record_frame()
    test_timeline_insert_clip_at_record_frame_edges()
    test_timeline_overwrite_clip_at_record_frame()
    test_timeline_overwrite_clip_at_record_frame_exact()
    test_timeline_overwrite_clip_at_record_frame_past_end()
    test_timeline_append_clip()
    test_timeline_record_start_of()
    print()
    print("All transport_logic tests passed.")
