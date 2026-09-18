"""Tests for timeline.py -- run directly: python3 test_timeline.py

Fully Qt-free.
"""
import stack as st
from timeline import Timeline, TimelineClip, next_clip_id


def _make_stack():
    stack = st.Stack()
    stack.add_stage(st.make_cdl_stage(slope=(1.1, 1.0, 0.9)))
    return stack


def test_clip_defaults_to_a_fresh_empty_stack():
    clip = TimelineClip("Shot 01", "/path/to/shot01.mov", "video", 0, 99)
    assert clip.stack.stages == []
    print("TimelineClip: with no stack given, defaults to a fresh empty Stack -- OK")


def test_clip_stack_is_live_not_a_snapshot():
    """The deliberate difference from StackMemory (see timeline.py's own
    module docstring): a clip's own Stack is directly, persistently
    editable in place -- mutating it through the SAME object later must
    be visible on the clip, unlike StackMemoryLibrary.save()'s own
    copy-on-save protection."""
    clip = TimelineClip("Shot 01", "/path/to/shot01.mov", "video", 0, 99)
    clip.stack.add_stage(st.make_cdl_stage(slope=(1.2, 1.0, 0.9)))
    assert len(clip.stack.stages) == 1
    clip.stack.add_stage(st.make_cdl_stage(sat=0.5))
    assert len(clip.stack.stages) == 2, "edits to clip.stack must accumulate in place, not reset/copy"
    print("TimelineClip: its own Stack is LIVE -- edits made directly on "
          "clip.stack persist and accumulate, no copy-on-save -- OK")


def test_clip_ids_are_unique_and_increasing():
    a = TimelineClip("A", "/a.mov", "video", 0, 10)
    b = TimelineClip("B", "/b.mov", "video", 0, 10)
    assert b.id != a.id
    assert b.id > a.id
    print("TimelineClip: ids are unique and monotonically increasing -- OK")


def test_clip_explicit_id_is_respected_not_reassigned():
    reserved = next_clip_id()
    clip = TimelineClip("A", "/a.mov", "video", 0, 10, clip_id=reserved)
    assert clip.id == reserved
    print("TimelineClip: an explicitly-passed clip_id is used as-is (for "
          "session load reconstruction), not silently reassigned -- OK")


def test_frame_count_is_inclusive_of_both_endpoints():
    assert TimelineClip("A", "/a.mov", "video", 0, 0).frame_count == 1
    assert TimelineClip("A", "/a.mov", "video", 0, 99).frame_count == 100
    assert TimelineClip("A", "/a.mov", "video", 50, 149).frame_count == 100
    print("TimelineClip.frame_count: inclusive of both in_frame and "
          "out_frame -- OK")


def test_frame_count_clamps_to_zero_for_an_inverted_range():
    clip = TimelineClip("Bad", "/a.mov", "video", 50, 10)
    assert clip.frame_count == 0
    print("TimelineClip.frame_count: clamps to 0 for an inverted/"
          "malformed in/out range rather than going negative -- OK")


def test_clip_copy_is_independent_with_the_same_id():
    clip = TimelineClip("Shot 01", "/a.mov", "video", 0, 99, stack=_make_stack())
    dup = clip.copy()
    assert dup.id == clip.id
    assert dup.stack is not clip.stack
    dup.stack.add_stage(st.make_cdl_stage(sat=0.5))
    assert len(clip.stack.stages) == 1, "editing the copy must not affect the original"
    print("TimelineClip.copy: same id, independent Stack -- editing the "
          "copy doesn't retroactively change the original -- OK")


def test_timeline_add_find_remove():
    timeline = Timeline()
    clip = timeline.add_clip(TimelineClip("Shot 01", "/a.mov", "video", 0, 99))
    assert timeline.find_clip(clip.id) is clip
    timeline.remove_clip(clip.id)
    assert timeline.clips == []
    print("Timeline: add_clip/find_clip/remove_clip -- OK")


def test_timeline_remove_unknown_id_raises_keyerror():
    timeline = Timeline()
    try:
        timeline.remove_clip(999999)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    print("Timeline.remove_clip: raises KeyError for an unknown id -- OK")


def test_timeline_find_unknown_id_raises_keyerror():
    timeline = Timeline()
    try:
        timeline.find_clip(999999)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    print("Timeline.find_clip: raises KeyError for an unknown id -- OK")


def test_timeline_add_clip_at_a_specific_index():
    timeline = Timeline()
    first = timeline.add_clip(TimelineClip("A", "/a.mov", "video", 0, 10))
    third = timeline.add_clip(TimelineClip("C", "/c.mov", "video", 0, 10))
    second = timeline.add_clip(TimelineClip("B", "/b.mov", "video", 0, 10), index=1)
    assert [c.id for c in timeline.clips] == [first.id, second.id, third.id]
    print("Timeline.add_clip: inserts at a specific index, not always "
          "appended -- OK")


def test_timeline_move_clip_reorders():
    timeline = Timeline()
    a = timeline.add_clip(TimelineClip("A", "/a.mov", "video", 0, 10))
    b = timeline.add_clip(TimelineClip("B", "/b.mov", "video", 0, 10))
    c = timeline.add_clip(TimelineClip("C", "/c.mov", "video", 0, 10))
    timeline.move_clip(c.id, 0)
    assert [clip.id for clip in timeline.clips] == [c.id, a.id, b.id]
    print("Timeline.move_clip: reorders to the target index -- OK")


def test_timeline_move_clip_clamps_out_of_range_index():
    timeline = Timeline()
    a = timeline.add_clip(TimelineClip("A", "/a.mov", "video", 0, 10))
    b = timeline.add_clip(TimelineClip("B", "/b.mov", "video", 0, 10))
    timeline.move_clip(a.id, 999)
    assert [clip.id for clip in timeline.clips] == [b.id, a.id]
    print("Timeline.move_clip: an out-of-range target index clamps to "
          "the end rather than raising -- OK")


def test_timeline_move_clip_unknown_id_raises_keyerror():
    timeline = Timeline()
    try:
        timeline.move_clip(999999, 0)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
    print("Timeline.move_clip: raises KeyError for an unknown id -- OK")


def test_timeline_total_frame_count_sums_every_clip():
    timeline = Timeline()
    timeline.add_clip(TimelineClip("A", "/a.mov", "video", 0, 99))    # 100 frames
    timeline.add_clip(TimelineClip("B", "/b.mov", "video", 0, 47))    # 48 frames
    assert timeline.total_frame_count() == 148
    print("Timeline.total_frame_count: sums every clip's own frame_count -- OK")


def test_timeline_copy_is_independent_with_the_same_clip_ids():
    timeline = Timeline(name="Reel 1")
    clip = timeline.add_clip(TimelineClip("A", "/a.mov", "video", 0, 10, stack=_make_stack()))
    dup = timeline.copy()
    assert dup.name == "Reel 1"
    assert dup.clips[0].id == clip.id
    assert dup.clips[0].stack is not clip.stack
    dup.clips[0].stack.add_stage(st.make_cdl_stage(sat=0.5))
    assert len(clip.stack.stages) == 1, "editing the copy's clip stack must not affect the original"
    print("Timeline.copy: independent clips (same ids, independent "
          "Stacks) -- OK")


def test_timeline_default_frame_rate():
    from timeline import DEFAULT_FRAME_RATE
    timeline = Timeline()
    assert timeline.frame_rate == DEFAULT_FRAME_RATE
    print("Timeline: defaults to DEFAULT_FRAME_RATE when not given -- OK")


if __name__ == "__main__":
    test_clip_defaults_to_a_fresh_empty_stack()
    test_clip_stack_is_live_not_a_snapshot()
    test_clip_ids_are_unique_and_increasing()
    test_clip_explicit_id_is_respected_not_reassigned()
    test_frame_count_is_inclusive_of_both_endpoints()
    test_frame_count_clamps_to_zero_for_an_inverted_range()
    test_clip_copy_is_independent_with_the_same_id()
    test_timeline_add_find_remove()
    test_timeline_remove_unknown_id_raises_keyerror()
    test_timeline_find_unknown_id_raises_keyerror()
    test_timeline_add_clip_at_a_specific_index()
    test_timeline_move_clip_reorders()
    test_timeline_move_clip_clamps_out_of_range_index()
    test_timeline_move_clip_unknown_id_raises_keyerror()
    test_timeline_total_frame_count_sums_every_clip()
    test_timeline_copy_is_independent_with_the_same_clip_ids()
    test_timeline_default_frame_rate()
    print()
    print("All timeline tests passed.")
