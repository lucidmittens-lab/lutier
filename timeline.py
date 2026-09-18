"""
timeline.py -- a "Continuity Grading Timeline": an ordered list of clips
(shots) the user builds up from the Image Library to review in cut order,
each carrying its OWN independent grade (a `stack.Stack`) that composes
with the app's existing main/global working Stack at playback time (see
stack.compose_stacks). Per Tom: this is explicitly NOT a full NLE -- no
transitions, no complex trim editing -- the point is checking a look
holds together across cuts, not producing an edit.

Mirrors stack_memory.py's own "a named thing that owns its own Stack"
shape (StackMemory) closely, with one deliberate difference: a
StackMemory is a SNAPSHOT (StackMemoryLibrary.save/recall always copies,
so editing a recalled stack can never retroactively change what was
saved) -- a TimelineClip's own Stack is LIVE and directly editable in
place (per Tom: "share one panel and make it a toggle... between
sequence and shot stack" -- StackPanel points itself at a clip's own
Stack object when the user toggles to "Shot" mode, no separate save-back
step). A freshly-added clip's Stack starts as a plain empty `Stack()`, so
there's nothing to protect by copying at construction time the way
StackMemoryLibrary.save() protects an actively-edited stack.

Qt-free, fully testable without a display -- same convention as
stack.py/stack_memory.py/session_io.py.
"""
import id_counter
import stack as st

# A Timeline is a single-kind list (only ever TimelineClip rows, unlike
# stack_memories_panel.py's StackMemory/GalleryStill/CDLEntry union list)
# -- the plain per-module counter shape applies directly, same as
# image_library.py/cdl_library.py/lut_library.py already use. No need for
# stack_memory.py's own next_memory_id()/next_gallery_still_id() shared-
# counter trick, which exists only to keep two DIFFERENT row kinds from
# colliding in the same union list.
next_clip_id = id_counter.make_next_entry_id()

# Paces (1) a still-kind clip's own hold duration -- a still has no native
# frame rate of its own, so its in_frame/out_frame are counted at THIS
# rate instead -- and (2) the overall timeline's own playback tick
# interval baseline (each video clip still DECODES at its own native fps
# internally; this is only the reference rate clip-to-clip advancement is
# paced against, not a per-clip frame-rate conversion). 24 matches this
# app's own DECODE_PROXY/default assumptions elsewhere (see video_
# playback_controller.py).
DEFAULT_FRAME_RATE = 24.0


class TimelineClip:
    """One shot in the timeline: a name, a path-referenced media source
    (re-decoded fresh, same "how to rebuild it, not the built thing"
    principle image_library.ImageLibraryEntry/stack_memory.GalleryStill
    already use for the identical reason -- can't be reconstructed if the
    source file has since moved), an in/out frame range into that source,
    and an OWNED, live `stack.Stack` -- this clip's own independent grade
    (see module docstring on why this is NOT a copy-on-save snapshot the
    way StackMemory is).

    `media_kind` reuses image_library.MEDIA_KINDS ("still"/"video") --
    deliberately not import-coupled to that module here (a plain string,
    same as ImageLibraryEntry's own field), so this module has no Qt-
    adjacent or app_logic dependency at all. `in_frame`/`out_frame` are
    always meaningful, even for a still (see DEFAULT_FRAME_RATE's own
    comment) -- a still-kind clip is just a source with exactly one
    decodable frame, held for `out_frame - in_frame + 1` ticks at the
    Timeline's own frame_rate."""

    def __init__(self, name, source_path, media_kind, in_frame, out_frame,
                 stack=None, clip_id=None, thumbnail=None):
        self.id = clip_id if clip_id is not None else next_clip_id()
        self.name = name
        self.source_path = source_path
        self.media_kind = media_kind
        self.in_frame = int(in_frame)
        self.out_frame = int(out_frame)
        self.stack = stack if stack is not None else st.Stack()
        self.thumbnail = thumbnail

    @property
    def frame_count(self):
        """Inclusive of both in_frame and out_frame (a clip trimmed to
        exactly one frame has in_frame == out_frame, frame_count == 1) --
        clamped to 0 rather than going negative for an inverted/malformed
        range, so a caller summing durations never has to guard against
        one bad clip corrupting the total."""
        return max(0, self.out_frame - self.in_frame + 1)

    def copy(self):
        """Same non-destructive shape as StackMemory.copy() -- a deep
        copy of this clip's own Stack, so a caller that duplicates a clip
        (or copies a whole Timeline, see Timeline.copy) can edit the copy
        without retroactively changing the original."""
        return TimelineClip(
            self.name, self.source_path, self.media_kind, self.in_frame, self.out_frame,
            stack=self.stack.copy(), clip_id=self.id, thumbnail=self.thumbnail)

    def __repr__(self):
        return (f"TimelineClip(id={self.id}, name={self.name!r}, media_kind={self.media_kind!r}, "
                f"in_frame={self.in_frame}, out_frame={self.out_frame}, "
                f"stages={len(self.stack.stages)})")


class Timeline:
    """An ordered list of TimelineClip entries, played back in order (hard
    cuts only, per module docstring). Editing API deliberately mirrors
    stack.Stack's own add_stage/remove_stage/find_stage/move_stage shape
    (same names, same "raise KeyError with the id" contract) rather than
    inventing a parallel vocabulary for what's structurally the same
    "ordered collection of id-identified things" problem."""

    def __init__(self, clips=None, name="Timeline", frame_rate=DEFAULT_FRAME_RATE):
        self.clips = list(clips) if clips is not None else []
        self.name = name
        self.frame_rate = frame_rate

    def add_clip(self, clip, index=None):
        """Insert `clip` at `index` (default: append at the end)."""
        if index is None:
            self.clips.append(clip)
        else:
            self.clips.insert(index, clip)
        return clip

    def remove_clip(self, clip_id):
        before = len(self.clips)
        self.clips = [c for c in self.clips if c.id != clip_id]
        if len(self.clips) == before:
            raise KeyError(f"No clip with id={clip_id}")

    def find_clip(self, clip_id):
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        raise KeyError(f"No clip with id={clip_id}")

    def move_clip(self, clip_id, new_index):
        """Reorder: pull the clip with `clip_id` out and reinsert it at
        `new_index` in the remaining list -- e.g. move_clip(id, 0) makes
        it first regardless of where it was. Same clamped-index shape as
        stack.Stack.move_stage."""
        idx = next((i for i, c in enumerate(self.clips) if c.id == clip_id), None)
        if idx is None:
            raise KeyError(f"No clip with id={clip_id}")
        clip = self.clips.pop(idx)
        new_index = max(0, min(new_index, len(self.clips)))
        self.clips.insert(new_index, clip)

    def total_frame_count(self):
        """Sum of every clip's own frame_count, at this Timeline's own
        frame_rate (see DEFAULT_FRAME_RATE's own comment on what that
        means for a still-kind clip specifically)."""
        return sum(c.frame_count for c in self.clips)

    def copy(self):
        return Timeline(clips=[c.copy() for c in self.clips], name=self.name,
                         frame_rate=self.frame_rate)

    def __repr__(self):
        return f"Timeline(name={self.name!r}, clips={len(self.clips)})"
