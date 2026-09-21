"""
edl.py -- CMX3600-style EDL (Edit Decision List) text generation from a
timeline.Timeline, per Tom: "add an EDL expression of the timeline in as
a sub panel." Same "Qt-free math as plain functions" split as timecode.
py/timeline_track_widget.py's own module-level functions -- no Qt
dependency at all, only timeline.Timeline/TimelineClip, independently
testable without a display.

A deliberately minimal, READ-ONLY expression of the current Timeline --
one event per clip, in Timeline order, with source in/out (the clip's own
in_frame/out_frame within its source media), record in/out (its position
within the whole Timeline, the same numbering TimelineController.
current_record_frame/TimelineTrackWidget's own record-space x-axis use),
and a comment line naming the source clip. NOT a round-trippable EDL
importer/exporter for exchange with other NLEs (no transitions, no audio
tracks, no real reel-name registry) -- same "groundwork, not the finished
feature" scope as timeline_track_widget.py's own docstring; this is a
readable expression of the edit, not an interchange format.
"""
import os

import timecode as tc_module

_EVENT_TRACK = "V"
_EVENT_TRANSITION = "C"  # cut -- no transition support yet


def clip_reel_name(clip):
    """An up-to-8-character reel-style name from the clip's own filename
    (no extension), upper-cased and stripped to alphanumerics -- CMX3600
    reel names are traditionally short/fixed-width. Falls back to "AX"
    (an EDL's conventional "auxiliary source" placeholder) for a clip
    with no usable source filename."""
    stem = os.path.splitext(os.path.basename(clip.source_path or ""))[0]
    stem = "".join(ch for ch in stem.upper() if ch.isalnum())
    return stem[:8] if stem else "AX"


def generate_edl(timeline_obj, title="LUTier Timeline"):
    """A CMX3600-style EDL string for `timeline_obj` -- one event per
    clip, in Timeline order. `title` becomes the EDL's own TITLE header.
    An empty Timeline still produces a valid header-only EDL (no events),
    rather than an empty string, so the sub-panel always has something
    coherent to show."""
    frame_rate = timeline_obj.frame_rate
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    record_frame = 0
    for index, clip in enumerate(timeline_obj.clips, start=1):
        src_in = clip.in_frame
        src_out = clip.out_frame + 1  # EDL out points are exclusive -- duration == out - in
        rec_in = record_frame
        rec_out = record_frame + clip.frame_count
        lines.append(
            f"{index:03d}  {clip_reel_name(clip):<8} {_EVENT_TRACK}     {_EVENT_TRANSITION}        "
            f"{tc_module.frames_to_timecode(src_in, frame_rate)} "
            f"{tc_module.frames_to_timecode(src_out, frame_rate)} "
            f"{tc_module.frames_to_timecode(rec_in, frame_rate)} "
            f"{tc_module.frames_to_timecode(rec_out, frame_rate)}")
        lines.append(f"* FROM CLIP NAME: {clip.name}")
        lines.append("")
        record_frame = rec_out
    return "\n".join(lines).rstrip("\n") + "\n"
