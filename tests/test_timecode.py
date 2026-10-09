"""Tests for timecode.py -- run directly: python3 -m tests.test_timecode

Fully Qt-free.
"""
from timecode import frames_to_timecode, timecode_to_frames, is_drop_frame_timecode


def test_non_drop_frame_basic_values():
    assert frames_to_timecode(0, 24) == "00:00:00:00"
    assert frames_to_timecode(23, 24) == "00:00:00:23"
    assert frames_to_timecode(24, 24) == "00:00:01:00"
    assert frames_to_timecode(24 * 60, 24) == "00:01:00:00"
    assert frames_to_timecode(24 * 3600, 24) == "01:00:00:00"
    print("frames_to_timecode: non-drop-frame basic values at 24fps -- OK")


def test_non_drop_frame_at_25_and_30fps():
    assert frames_to_timecode(25, 25) == "00:00:01:00"
    assert frames_to_timecode(30, 30) == "00:00:01:00"
    print("frames_to_timecode: non-drop-frame at 25/30fps (exact integer "
          "rates, never auto-select drop-frame) -- OK")


def test_drop_frame_auto_selected_for_29_97():
    """29.97fps must auto-select drop-frame (semicolon separator) with
    no explicit drop_frame argument."""
    tc = frames_to_timecode(1800, 29.97)
    assert ";" in tc
    print("frames_to_timecode: 29.97fps auto-selects drop-frame -- OK")


def test_drop_frame_known_reference_value_one_minute():
    """The textbook drop-frame reference case: frame 1800 at 29.97fps
    (nominal 30fps timecode) must read 00:01:00;02 -- frame numbers
    00 and 01 of minute 1 are dropped, so the count jumps from
    00:00:59;29 straight to 00:01:00;02."""
    assert frames_to_timecode(1800, 29.97) == "00:01:00;02"
    print("frames_to_timecode: matches the well-known 00:01:00;02 "
          "drop-frame reference value at frame 1800/29.97fps -- OK")


def test_drop_frame_tenth_minute_is_not_dropped():
    """Exactly 10 real minutes elapsed (17982 real frames at 29.97fps)
    must land on 00:10:00;00 -- the 10th minute is the one that does
    NOT drop frame numbers, per the SMPTE spec."""
    assert frames_to_timecode(17982, 29.97) == "00:10:00;00"
    print("frames_to_timecode: the 10th minute boundary is NOT dropped "
          "(00:10:00;00) -- OK")


def test_drop_frame_frame_before_the_drop():
    """The frame immediately before a drop (end of minute 0) must still
    read as a plain, un-adjusted count."""
    assert frames_to_timecode(1799, 29.97) == "00:00:59;29"
    print("frames_to_timecode: the frame just before a minute boundary "
          "drop is unaffected (00:00:59;29) -- OK")


def test_drop_frame_at_59_94_uses_four_dropped_frame_numbers():
    """59.94fps drops 4 frame numbers per non-tenth minute (double
    29.97's 2, matching the doubled nominal frame rate)."""
    tc = frames_to_timecode(3600, 59.94)  # frame 3600 == 1 minute in
    assert tc == "00:01:00;04"
    print("frames_to_timecode: 59.94fps drop-frame drops 4 frame "
          "numbers per minute (00:01:00;04) -- OK")


def test_explicit_drop_frame_override():
    non_drop = frames_to_timecode(1800, 29.97, drop_frame=False)
    assert non_drop == "00:01:00:00"
    assert ";" not in non_drop
    drop = frames_to_timecode(1800, 24, drop_frame=True)
    assert ";" in drop
    print("frames_to_timecode: explicit drop_frame=True/False overrides "
          "the frame_rate auto-detection -- OK")


def test_wraps_at_24_hours():
    fr = 24
    one_day_frames = fr * 3600 * 24
    assert frames_to_timecode(one_day_frames, fr) == "00:00:00:00"
    print("frames_to_timecode: wraps back to 00:00:00:00 at 24 hours -- OK")


def test_zero_or_negative_frame_rate_returns_zero_timecode():
    assert frames_to_timecode(100, 0) == "00:00:00:00"
    assert frames_to_timecode(100, -5) == "00:00:00:00"
    print("frames_to_timecode: a zero/negative frame_rate returns "
          "00:00:00:00 rather than raising -- OK")


def test_timecode_to_frames_inverts_frames_to_timecode():
    for rate in (23.976, 24, 25, 29.97, 30, 59.94):
        for frame in (0, 1, 1799, 1800, 17982, 107892, 215784):
            text = frames_to_timecode(frame, rate)
            assert timecode_to_frames(text, rate) == frame, (rate, frame, text)
    print("timecode_to_frames: inverts frames_to_timecode, drop and non-drop -- OK")


def test_timecode_to_frames_reads_drop_frame_from_the_separator():
    # A source that wrote ":" at 29.97 is non-drop, whatever the rate says.
    assert timecode_to_frames("00:01:00:02", 29.97) == 30 * 60 + 2
    assert timecode_to_frames("00:01:00;02", 29.97) == 1800
    assert is_drop_frame_timecode("00:01:00;02") and not is_drop_frame_timecode("00:01:00:02")
    print("timecode_to_frames: drop-frame comes from the ';' separator -- OK")


def test_timecode_to_frames_rejects_garbage():
    for text in ("", None, "1:00", "aa:bb:cc:dd"):
        assert timecode_to_frames(text, 24) is None
    assert timecode_to_frames("00:00:01:00", 0) is None
    print("timecode_to_frames: unparseable text or no rate -> None -- OK")


if __name__ == "__main__":
    test_non_drop_frame_basic_values()
    test_non_drop_frame_at_25_and_30fps()
    test_drop_frame_auto_selected_for_29_97()
    test_drop_frame_known_reference_value_one_minute()
    test_drop_frame_tenth_minute_is_not_dropped()
    test_drop_frame_frame_before_the_drop()
    test_drop_frame_at_59_94_uses_four_dropped_frame_numbers()
    test_explicit_drop_frame_override()
    test_wraps_at_24_hours()
    test_zero_or_negative_frame_rate_returns_zero_timecode()
    test_timecode_to_frames_inverts_frames_to_timecode()
    test_timecode_to_frames_reads_drop_frame_from_the_separator()
    test_timecode_to_frames_rejects_garbage()
    print()
    print("All timecode tests passed.")
