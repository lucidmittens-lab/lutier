"""Tests for edl.py -- run directly:
python3 -m tests.test_edl

Qt-free, same as edl.py itself -- no QApplication needed anywhere here.
"""
import timeline as tl
from edl import clip_reel_name, generate_edl


def _clip(name, source_path, in_frame, out_frame, media_kind="video"):
    return tl.TimelineClip(name, source_path, media_kind, in_frame, out_frame)


def test_clip_reel_name_uses_the_uppercased_filename_stem():
    clip = _clip("A", "/tmp/some_footage.mp4", 0, 9)
    assert clip_reel_name(clip) == "SOMEFOOT", "alphanumeric-only, upper-cased, truncated to 8 chars"
    print("clip_reel_name: derives an 8-char reel name from the source filename -- OK")


def test_clip_reel_name_falls_back_to_ax_for_no_usable_name():
    clip = _clip("Untitled", "", 0, 9)
    assert clip_reel_name(clip) == "AX"
    print("clip_reel_name: falls back to AX (EDL's conventional aux-source placeholder) -- OK")


def test_generate_edl_on_an_empty_timeline_is_still_a_valid_header():
    timeline_obj = tl.Timeline()
    text = generate_edl(timeline_obj, title="Empty")
    assert text.startswith("TITLE: Empty\n")
    assert "FCM: NON-DROP FRAME" in text
    assert "001" not in text
    print("generate_edl: an empty Timeline still produces a valid header-only EDL -- OK")


def test_generate_edl_has_one_numbered_event_per_clip_in_order():
    timeline_obj = tl.Timeline(
        clips=[_clip("A", "/tmp/a.mp4", 0, 9), _clip("B", "/tmp/b.mp4", 0, 4)],
        frame_rate=24)
    text = generate_edl(timeline_obj)
    assert "001" in text and "002" in text
    assert text.index("001") < text.index("002")
    assert "* FROM CLIP NAME: A" in text
    assert "* FROM CLIP NAME: B" in text
    print("generate_edl: one numbered event per clip, in Timeline order -- OK")


def test_generate_edl_record_timecodes_accumulate_across_clips():
    """The second clip's own record IN must start exactly where the
    first clip's record OUT left off -- same running-total record-space
    numbering timeline_track_widget.clip_extents and TimelineController.
    current_record_frame both already use."""
    timeline_obj = tl.Timeline(
        clips=[_clip("A", "/tmp/a.mp4", 0, 9), _clip("B", "/tmp/b.mp4", 0, 4)],
        frame_rate=24)  # A: 10 frames (0..9) -> record 0..10; B: 5 frames -> record 10..15
    text = generate_edl(timeline_obj)
    lines = [line for line in text.splitlines() if line.startswith(("001", "002"))]
    event_a, event_b = lines
    # Event line fields: number, reel, track, transition, src_in, src_out, rec_in, rec_out
    rec_out_a = event_a.split()[-1]
    rec_in_b = event_b.split()[-2]
    assert rec_out_a == rec_in_b, "clip B's record IN must equal clip A's record OUT"
    print("generate_edl: record timecodes accumulate across clips with "
          "no gap or overlap -- OK")


def test_generate_edl_source_in_out_reflect_the_clips_own_trim():
    """A clip trimmed to a sub-range of its source (in_frame > 0) must
    show ITS OWN in_frame/out_frame in the EDL's source columns, not the
    source media's frame 0."""
    import timecode as tc_module
    timeline_obj = tl.Timeline(clips=[_clip("Trimmed", "/tmp/a.mp4", 100, 149)], frame_rate=24)
    text = generate_edl(timeline_obj)
    event_line = next(line for line in text.splitlines() if line.startswith("001"))
    src_in, src_out = event_line.split()[4], event_line.split()[5]
    assert src_in == tc_module.frames_to_timecode(100, 24)
    assert src_out == tc_module.frames_to_timecode(150, 24), "EDL out points are exclusive (out_frame + 1)"
    print("generate_edl: source in/out reflect the clip's own trim, not "
          "the source media's frame 0 -- OK")


if __name__ == "__main__":
    test_clip_reel_name_uses_the_uppercased_filename_stem()
    test_clip_reel_name_falls_back_to_ax_for_no_usable_name()
    test_generate_edl_on_an_empty_timeline_is_still_a_valid_header()
    test_generate_edl_has_one_numbered_event_per_clip_in_order()
    test_generate_edl_record_timecodes_accumulate_across_clips()
    test_generate_edl_source_in_out_reflect_the_clips_own_trim()
    print()
    print("All edl tests passed.")
