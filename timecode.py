"""
timecode.py -- SMPTE-style timecode formatting (Phase 4 transport
upgrade, per Tom: "real SMPTE timecode"), replacing the transport bar's
"N / total" label. Qt-free, fully testable without a display -- same
convention as every other backend module in this app.

DROP-FRAME: real NTSC-derived frame rates (29.97/59.94fps) don't evenly
divide a wall-clock second, so a naive frame-count timecode drifts from
real elapsed time over a long clip -- SMPTE's drop-frame convention
periodically skips FRAME NUMBERS (never actual frames -- nothing is
dropped from playback or decode, only from the on-screen numbering) to
keep timecode in sync with wall-clock time: frame numbers 0 and 1 are
skipped at the start of every minute EXCEPT every 10th minute (17982
real frames per 10 minutes at 29.97fps don't divide evenly into 18000
nominal 30fps-slots -- dropping 18 numbers per 10-minute block, 2 per
non-tenth minute, reconciles the two). Auto-detected from frame_rate
unless explicitly overridden via `drop_frame` -- see frames_to_timecode.
Drop-frame timecode conventionally uses a semicolon before the frames
field (HH:MM:SS;FF) instead of a colon, to mark it as drop-frame at a
glance -- followed here too.
"""

# Frame rates close enough to a real NTSC-derived rate to auto-select
# drop-frame timecode -- exact integer rates (24/25/30/50/60fps) never
# drift the way 29.97/59.94 do, so they stay non-drop by default.
_DROP_FRAME_RATES = (29.97, 59.94)
# Tight enough to tell 29.97 apart from the exact-integer 30fps it's
# derived from (they differ by 0.03) -- a real bug caught by testing:
# 0.05 was wide enough to misclassify a plain 30fps source as drop-frame.
_DROP_FRAME_TOLERANCE = 0.01


def _is_drop_frame_rate(frame_rate):
    return any(abs(frame_rate - r) < _DROP_FRAME_TOLERANCE for r in _DROP_FRAME_RATES)


def frames_to_timecode(frame_index, frame_rate, drop_frame=None):
    """frame_index (a plain 0-based elapsed frame count, e.g.
    VideoPlaybackController._frame_index) -> "HH:MM:SS:FF" (non-drop) or
    "HH:MM:SS;FF" (drop-frame). `drop_frame=None` (default) auto-selects
    based on frame_rate (see _is_drop_frame_rate) -- pass True/False to
    override. frame_rate <= 0 returns "00:00:00:00" rather than raising
    (no meaningful timecode without a real rate). Wraps at 24 hours, same
    convention broadcast timecode uses."""
    if frame_rate <= 0:
        return "00:00:00:00"
    if drop_frame is None:
        drop_frame = _is_drop_frame_rate(frame_rate)
    if drop_frame:
        return _drop_frame_timecode(frame_index, frame_rate)
    return _non_drop_frame_timecode(frame_index, frame_rate)


def _hms(adjusted_frame_index, fr):
    frames = adjusted_frame_index % fr
    total_seconds = adjusted_frame_index // fr
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = (total_seconds // 60 // 60) % 24
    return hours, minutes, seconds, frames


def _non_drop_frame_timecode(frame_index, frame_rate):
    fr = round(frame_rate)
    hours, minutes, seconds, frames = _hms(frame_index, fr)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def _drop_frame_timecode(frame_index, frame_rate):
    """Standard SMPTE drop-frame algorithm -- renumbers real elapsed
    frames into nominal-fps (30 for 29.97, 60 for 59.94) timecode slots,
    dropping `drop_frames` frame NUMBERS at the start of every minute
    except every 10th. frames_per_10_min is computed from the REAL
    (non-integer) frame_rate -- e.g. round(29.97 * 600) = 17982, not
    round(30 * 600) = 18000 -- since that's the actual elapsed-frame
    count 10 real minutes covers; using the rounded rate there would
    silently produce wrong timecodes (a real mistake caught by checking
    this against the well-known 00:01:00;02 reference case, see
    test_timecode.py)."""
    fr = round(frame_rate)
    drop_frames = round(frame_rate * 0.066666)  # 2 for 29.97, 4 for 59.94
    frames_per_min = fr * 60 - drop_frames
    frames_per_10_min = round(frame_rate * 60 * 10)
    drop_per_10_min_block = drop_frames * 9  # 9 non-tenth minutes per block

    ten_min_blocks, remainder = divmod(frame_index, frames_per_10_min)
    if remainder >= drop_frames:
        adjusted = (frame_index + drop_per_10_min_block * ten_min_blocks
                    + drop_frames * ((remainder - drop_frames) // frames_per_min))
    else:
        adjusted = frame_index + drop_per_10_min_block * ten_min_blocks

    hours, minutes, seconds, frames = _hms(adjusted, fr)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d};{frames:02d}"


def timecode_to_frames(text, frame_rate):
    """Inverse of frames_to_timecode: "HH:MM:SS:FF" / "HH:MM:SS;FF" ->
    0-based frame count, or None if `text` doesn't parse or frame_rate
    <= 0. Drop-frame is read from the string itself (a ";" or "." before
    the frames field, the SMPTE marking a camera/container writes), not
    auto-detected from frame_rate -- a source that wrote non-drop at
    29.97 means non-drop. Callers formatting a related timecode (e.g.
    a clip's End TC from its Start TC) should pass the same
    is_drop_frame_timecode(text) to frames_to_timecode."""
    if frame_rate <= 0 or not text:
        return None
    parts = text.strip().replace(";", ":").replace(".", ":").split(":")
    if len(parts) != 4:
        return None
    try:
        hours, minutes, seconds, frames = (int(p) for p in parts)
    except ValueError:
        return None
    fr = round(frame_rate)
    nominal = ((hours * 60 + minutes) * 60 + seconds) * fr + frames
    if not is_drop_frame_timecode(text):
        return nominal
    drop_frames = round(frame_rate * 0.066666)
    total_minutes = hours * 60 + minutes
    return nominal - drop_frames * (total_minutes - total_minutes // 10)


def is_drop_frame_timecode(text):
    return bool(text) and (";" in text or "." in text)
