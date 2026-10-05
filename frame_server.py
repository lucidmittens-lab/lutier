"""
frame_server.py -- decodes playback frames AHEAD on a worker thread, so
the UI thread never waits on a decoder while playing (Tom, 2026-10-05:
"make the performance feel more slick").

Before this, both playback controllers decoded each frame on the UI
thread inside their QTimer tick (~18 ms for a 4K ProRes 4444 proxy, of a
41 ms budget at 24 fps), and opened the NEXT clip's file synchronously
at every cut -- any other UI work, or a cut, showed up as a stutter.

A FrameServer is given a play order -- a list of Segments (one source
range each: a whole Timeline, or one clip's in/out range) -- and a start
position, and keeps up to `capacity` decoded frames buffered ahead of
it. A video segment's file is opened (and seeked) by the worker before
playback reaches it, so cuts cost nothing on the UI thread. A still
segment produces ONE frame (its image) at offset 0; the controllers
hold it themselves.

The UI side only ever calls take(): it returns the buffered frame for a
position (dropping anything buffered before it) or None if it isn't
decoded yet -- callers then decode it themselves (the old synchronous
path) and call restart() from there, so a miss is never WRONG, only as
slow as before. Frames are float32 RGB arrays exactly as VideoSource /
app_logic produce them (already capped to `max_dimension`).

PyAV and numpy release the GIL for the heavy parts (decode, scaling,
copies), so the worker really does run alongside the UI thread.
"""
import threading
from collections import OrderedDict, namedtuple

import app_logic
import video_source

# path, "video" or "still", the segment's first SOURCE frame, its length
# in frames (a still's length is ignored -- it yields one frame).
Segment = namedtuple("Segment", "path kind first_frame frame_count")


class FrameServer:
    def __init__(self, max_dimension, capacity=24):
        self.max_dimension = max_dimension
        self.capacity = max(2, int(capacity))
        self._cond = threading.Condition()
        self._buffer = OrderedDict()   # (segment_index, offset) -> array, in play order
        self._generation = 0           # bumped by restart/stop; a stale worker exits
        self._thread = None
        self._segments = []
        self._step = 1
        self._loop = False

    # ---- UI-thread API ------------------------------------------------------

    def restart(self, segments, segment_index, offset, step=1, loop=False):
        """(Re)start decoding ahead from (segment_index, offset) along
        `segments`, advancing `step` frames at a time and wrapping to the
        start when `loop`. Discards anything buffered for an old plan."""
        with self._cond:
            self._generation += 1
            generation = self._generation
            self._buffer.clear()
            self._segments = list(segments)
            self._step = max(1, int(step))
            self._loop = bool(loop)
            self._cond.notify_all()
        self._thread = threading.Thread(
            target=self._run, args=(generation, segment_index, offset),
            name="lutier-frame-server", daemon=True)
        self._thread.start()

    def stop(self):
        with self._cond:
            self._generation += 1
            self._buffer.clear()
            self._cond.notify_all()
        self._thread = None

    def take(self, segment_index, offset):
        """The decoded frame for (segment_index, offset), or None if it
        isn't buffered. Drops every frame buffered BEFORE it (play order),
        freeing room for the worker to keep decoding ahead."""
        key = (segment_index, offset)
        with self._cond:
            if key not in self._buffer:
                return None
            while True:
                head, frame = self._buffer.popitem(last=False)
                if head == key:
                    self._cond.notify_all()
                    return frame

    def take_latest_up_to(self, segment_index, offset):
        """(offset, frame) for the NEWEST buffered frame of this segment at
        or before `offset` (dropping everything buffered before it), or
        None if there's none -- what a behind-schedule playback tick shows
        instead of waiting for the exact frame."""
        with self._cond:
            best = None
            for (seg, off) in self._buffer:
                if (seg, off) > (segment_index, offset):
                    break
                if seg == segment_index:
                    best = off
            if best is None:
                return None
            key = (segment_index, best)
            while True:
                head, frame = self._buffer.popitem(last=False)
                if head == key:
                    self._cond.notify_all()
                    return best, frame

    def buffered_count(self):
        with self._cond:
            return len(self._buffer)

    # ---- worker -----------------------------------------------------------

    def _alive(self, generation):
        return generation == self._generation

    def _put(self, generation, key, frame):
        """Blocks while the buffer is full; False if the plan changed."""
        with self._cond:
            while self._alive(generation) and len(self._buffer) >= self.capacity:
                self._cond.wait(0.1)
            if not self._alive(generation):
                return False
            self._buffer[key] = frame
            return True

    def _run(self, generation, segment_index, offset):
        source = None
        source_segment = None
        decoder_next = None  # source frame decode_next_frame would return next
        try:
            while self._alive(generation):
                if segment_index >= len(self._segments):
                    if not self._loop or not self._segments:
                        return
                    segment_index, offset = 0, 0
                segment = self._segments[segment_index]

                if segment.kind == "still":
                    try:
                        frame = app_logic.load_image_as_array(segment.path, max_dimension=self.max_dimension)
                    except Exception:  # noqa: BLE001 -- the UI's own fallback decode reports it
                        return
                    if not self._put(generation, (segment_index, 0), frame):
                        return
                    segment_index, offset = segment_index + 1, 0
                    continue

                if offset >= segment.frame_count:
                    segment_index, offset = segment_index + 1, 0
                    continue

                wanted = segment.first_frame + offset
                if source_segment != segment_index:
                    if source is not None:
                        source.close()
                    try:
                        source = video_source.VideoSource(segment.path)
                    except Exception:  # noqa: BLE001 -- see above
                        return
                    source_segment = segment_index
                    decoder_next = None
                # Sequential decode only when it lands on the wanted frame;
                # anything else (a new segment, a loop back to the start of
                # the same one, a step > 1) is a seek.
                if wanted == decoder_next:
                    frame = source.decode_next_frame(max_dimension=self.max_dimension)
                else:
                    frame = source.seek_to_frame(wanted, max_dimension=self.max_dimension)
                decoder_next = wanted + 1

                if frame is None:  # ran out early (frame_count was an estimate)
                    segment_index, offset = segment_index + 1, 0
                    continue
                if not self._put(generation, (segment_index, offset), frame):
                    return
                offset += self._step
        finally:
            if source is not None:
                source.close()
